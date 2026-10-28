"""A small Parquet lake for change events: append-only bronze changelog + a rebuilt current-state (silver) table.

Bronze files are named by the LSN range they cover and written atomically (temp file + rename), so replaying a batch after a
crash rewrites the same file and never leaves a partial one. Silver is derived: the newest event per key (by commit LSN,
then position in the transaction), deletes removed, duplicate events collapsed. Column names are prefixed in bronze
(`k__` key, `a__` after-image) so a source column can never collide with metadata columns.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

PA_TYPES = {
    "integer": pa.int32(), "bigint": pa.int64(), "smallint": pa.int16(), "boolean": pa.bool_(),
    "numeric": pa.decimal128(38, 9), "double precision": pa.float64(), "real": pa.float64(),
}  # fmt: skip


def arrow_type(pg_type: str) -> pa.DataType:
    return PA_TYPES.get(pg_type, pa.string())


def _q(path: Path | str) -> str:
    return "'" + str(path).replace("'", "''") + "'"


class Lake:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def bronze_dir(self, table: str) -> Path:
        d = self.root / "bronze" / table
        d.mkdir(parents=True, exist_ok=True)
        return d

    def silver_path(self, table: str) -> Path:
        d = self.root / "silver" / table
        d.mkdir(parents=True, exist_ok=True)
        return d / "current.parquet"

    def append_changes(
        self, table: str, key: list[str], events: list[dict], types: dict[str, str], name: str
    ) -> Path:
        """events: envelopes (op/before/after/source/ts). `types`: column -> postgres type, for every column seen."""
        cols = sorted(types)
        data: dict[str, list] = {
            "commit_lsn": [e["source"]["lsn"] for e in events],
            "ordinal": [e["source"]["ordinal"] for e in events],
            "xid": [e["source"]["txId"] for e in events],
            "op": [e["op"] for e in events],
            "ts": [e["ts"] for e in events],
        }
        fields = [
            ("commit_lsn", pa.int64()),
            ("ordinal", pa.int32()),
            ("xid", pa.int64()),
            ("op", pa.string()),
            ("ts", pa.string()),
        ]
        for k in key:
            data[f"k__{k}"] = [(e["after"] or e["before"])[k] for e in events]
            fields.append((f"k__{k}", arrow_type(types[k])))
        for c in cols:
            data[f"a__{c}"] = [None if e["after"] is None else e["after"].get(c) for e in events]
            fields.append((f"a__{c}", arrow_type(types[c])))
        arr = [pa.array(_scale(data[n], t), type=t) for n, t in fields]
        out = self.bronze_dir(table) / f"{name}.parquet"
        tmp = out.with_suffix(".tmp")
        pq.write_table(pa.Table.from_arrays(arr, names=[n for n, _ in fields]), tmp)
        os.replace(tmp, out)
        (self.root / "bronze" / table / "_key.json").write_text(json.dumps(key))
        return out

    def key(self, table: str) -> list[str]:
        return json.loads((self.root / "bronze" / table / "_key.json").read_text())

    def rebuild_current(self, table: str) -> int:
        key = self.key(table)
        part = ", ".join(f"k__{k}" for k in key)
        files = _q(self.bronze_dir(table) / "*.parquet")
        con = duckdb.connect()
        out = self.silver_path(table)
        tmp = out.with_suffix(".tmp")
        con.execute(f"""
        COPY (
          SELECT * EXCLUDE (rn, rn0, commit_lsn, ordinal, xid, op, ts) FROM (
            SELECT *, row_number() OVER (PARTITION BY {part} ORDER BY commit_lsn DESC, ordinal DESC) AS rn FROM (
              SELECT *, row_number() OVER (PARTITION BY commit_lsn, ordinal, {part}) AS rn0
              FROM read_parquet({files}, union_by_name=true))
            WHERE rn0 = 1)
          WHERE rn = 1 AND op <> 'd'
          ORDER BY {part}
        ) TO {_q(tmp)} (FORMAT parquet)""")
        con.close()
        os.replace(tmp, out)
        return len(self.current(table))

    def current(self, table: str) -> list[dict]:
        key = self.key(table)
        con = duckdb.connect()
        cur = con.execute(f"SELECT * FROM read_parquet({_q(self.silver_path(table))})")
        names = [d[0] for d in cur.description]
        rows = cur.fetchall()
        con.close()
        key_idx = {f"k__{k}" for k in key}
        keep = [i for i, n in enumerate(names) if n not in key_idx]
        return [{names[i].removeprefix("a__"): r[i] for i in keep} for r in rows]

    def changelog(self, table: str) -> list[tuple]:
        con = duckdb.connect()
        rows = con.execute(
            f"SELECT commit_lsn, ordinal, op FROM read_parquet({_q(self.bronze_dir(table) / '*.parquet')}, union_by_name=true) ORDER BY 1, 2"
        ).fetchall()
        con.close()
        return rows

    def bronze_files(self, table: str) -> list[str]:
        return sorted(p.name for p in self.bronze_dir(table).glob("*.parquet"))


def _scale(values: list, t: pa.DataType) -> list:
    if pa.types.is_decimal(t):
        return [None if v is None else Decimal(v).quantize(Decimal("1e-9")) for v in values]
    return values
