"""COPY semantics for the local warehouse (DuckDB), plus the Redshift COPY statement the real pipeline would run.

Redshift COPY is transactional (all rows or none past MAXERROR) and NOT idempotent: loading the same file twice duplicates
rows. The ledger below (file hash per entity) is the mitigation a real pipeline needs, and it is what is tested here.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

TABLES = {
    "orders": [("order_id", "BIGINT"), ("customer_id", "BIGINT"), ("order_date", "DATE"), ("status", "VARCHAR"), ("amount", "DECIMAL(12,2)")],
    "customers": [("customer_id", "BIGINT"), ("segment", "VARCHAR"), ("country", "VARCHAR")],
}  # fmt: skip
IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
ROLE = re.compile(r"^arn:aws:iam::\d{12}:role/[\w+=,.@/-]{1,64}$")
S3URI = re.compile(r"^s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]/[\w!\-./*]+$")


class CopyError(RuntimeError):
    pass


def copy_sql(
    table: str,
    s3_uri: str,
    iam_role_arn: str,
    *,
    schema: str = "raw",
    maxerror: int = 0,
    ignoreheader: int = 1,
) -> str:
    """The Redshift COPY the production loader would issue. Inputs are validated; nothing is string-interpolated unchecked."""
    if not (IDENT.match(schema) and IDENT.match(table)):
        raise ValueError("invalid identifier")
    if not S3URI.match(s3_uri):
        raise ValueError("invalid S3 URI")
    if not ROLE.match(iam_role_arn):
        raise ValueError("invalid IAM role ARN")
    if not (
        isinstance(maxerror, int)
        and 0 <= maxerror <= 1000
        and isinstance(ignoreheader, int)
        and 0 <= ignoreheader <= 1
    ):
        raise ValueError("invalid COPY options")
    return (
        f"COPY {schema}.{table} FROM '{s3_uri}' IAM_ROLE '{iam_role_arn}' FORMAT AS CSV "
        f"IGNOREHEADER {ignoreheader} MAXERROR {maxerror} TIMEFORMAT 'auto' TRUNCATECOLUMNS"
    )


class Warehouse:
    def __init__(self, path: str | Path):
        self.con = duckdb.connect(str(path))
        self.con.execute("CREATE SCHEMA IF NOT EXISTS raw")
        for t, cols in TABLES.items():
            ddl = ", ".join(f"{c} {ty}" for c, ty in cols)
            self.con.execute(
                f"CREATE TABLE IF NOT EXISTS raw.{t} ({ddl}, _batch_id VARCHAR, _loaded_at TIMESTAMP, _effective_date DATE)"
            )
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS raw.load_errors (entity VARCHAR, batch_id VARCHAR, line BIGINT, column_name VARCHAR, raw_value VARCHAR)"
        )
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS raw.copy_ledger (entity VARCHAR, batch_id VARCHAR, sha256 VARCHAR, rows_loaded BIGINT, rows_rejected BIGINT, PRIMARY KEY (entity, batch_id))"
        )
        self._clock = 0

    def close(self) -> None:
        self.con.close()

    def copy(
        self,
        entity: str,
        csv_path: str | Path,
        batch_id: str,
        *,
        maxerror: int = 0,
        effective_date: str | None = None,
    ) -> dict:
        """Load one file atomically. Returns {'status': 'loaded'|'skipped', ...}; raises CopyError past `maxerror`."""
        cols = TABLES[entity]
        digest = hashlib.sha256(Path(csv_path).read_bytes()).hexdigest()
        row = self.con.execute(
            "SELECT sha256 FROM raw.copy_ledger WHERE entity=? AND batch_id=?", [entity, batch_id]
        ).fetchone()
        if row:
            if row[0] != digest:
                raise CopyError(f"{entity}/{batch_id} was loaded with different content")
            return {"status": "skipped", "rows_loaded": 0, "rows_rejected": 0}
        casts = ", ".join(f"TRY_CAST({c} AS {ty}) AS {c}" for c, ty in cols)
        loaded_at = datetime.now(UTC).replace(tzinfo=None, microsecond=0) + timedelta(
            seconds=self._clock
        )
        self._clock += (
            1  # strictly increasing load timestamps, so "latest load wins" is deterministic
        )
        self.con.execute("BEGIN")
        try:
            self.con.execute(
                "CREATE TEMP TABLE _stage AS SELECT row_number() OVER () + 1 AS line, * FROM read_csv(?, header=true, all_varchar=true, nullstr='')",
                [str(csv_path)],
            )
            bad = self._bad_cells(cols)
            bad_lines = {b[0] for b in bad}
            if len(bad_lines) > maxerror:
                raise CopyError(
                    f"{entity}/{batch_id}: {len(bad_lines)} rejected rows exceed MAXERROR {maxerror}; nothing loaded"
                )
            self.con.execute(
                f"INSERT INTO raw.{entity} SELECT {casts}, ?, ?, TRY_CAST(? AS DATE) FROM _stage WHERE line NOT IN (SELECT UNNEST(?::BIGINT[]))",
                [batch_id, loaded_at, effective_date, sorted(bad_lines)],
            )
            if bad:
                self.con.executemany(
                    "INSERT INTO raw.load_errors VALUES (?,?,?,?,?)",
                    [(entity, batch_id, ln, col, val) for ln, col, val in bad],
                )
            total = self.con.execute("SELECT COUNT(*) FROM _stage").fetchone()[0]
            self.con.execute(
                "INSERT INTO raw.copy_ledger VALUES (?,?,?,?,?)",
                [entity, batch_id, digest, total - len(bad_lines), len(bad_lines)],
            )
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise
        finally:
            self.con.execute("DROP TABLE IF EXISTS _stage")
        return {
            "status": "loaded",
            "rows_loaded": total - len(bad_lines),
            "rows_rejected": len(bad_lines),
        }

    def _bad_cells(self, cols):
        out = []
        for c, ty in cols:
            out += self.con.execute(
                f"SELECT line, '{c}', {c} FROM _stage WHERE {c} IS NOT NULL AND TRY_CAST({c} AS {ty}) IS NULL"
            ).fetchall()
        return out

    def load_landing(self, landing: str | Path, *, maxerror: int = 5) -> list[dict]:
        out = []
        for d in sorted(p for p in Path(landing).iterdir() if p.is_dir()):
            meta = json.loads((d / "_meta.json").read_text()) if (d / "_meta.json").exists() else {}
            for entity in ("customers", "orders"):
                res = self.copy(
                    entity,
                    d / f"{entity}.csv",
                    d.name,
                    maxerror=maxerror,
                    effective_date=meta.get("effective_date"),
                )
                out.append({"batch": d.name, "entity": entity, **res})
        return out
