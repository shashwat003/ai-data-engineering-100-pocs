"""Read-only dataset catalogue over a SQLite database plus a YAML metadata file.

Security boundaries (each enforced here, not left to the caller):
- The database is opened mode=ro with an authorizer that allows only SELECT/READ on the requested
  dataset (and its upstream tables, for views) and denies every column flagged `pii`.
- Identifiers (dataset, column) are looked up in the catalogue first; only catalogue-known names
  are ever placed in SQL, always double-quoted. There is no free-form SQL anywhere.
- PII columns are never read: samples return "[REDACTED]" and profiling refuses them.
- Row counts, sample sizes, search results and query time are bounded.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
import yaml
from sqlglot import exp

from retrieval_kit import BM25Index

MAX_SAMPLE_ROWS = 10
MAX_TOP_VALUES = 5
REDACTED = "[REDACTED]"
_SAFE_FUNCTIONS = frozenset({"count", "min", "max", "sum", "avg", "coalesce", "round"})


class CatalogueError(ValueError):
    """A request the catalogue refuses (unknown name, restricted column, bad bound)."""


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str
    description: str
    pii: bool
    primary_key: bool


@dataclass(frozen=True)
class Dataset:
    name: str
    kind: str  # table | view
    description: str
    domain: str
    owner: str
    tags: tuple[str, ...]
    classification: str
    freshness_hours: int | None
    columns: tuple[ColumnInfo, ...]
    row_count: int
    upstream: tuple[str, ...] = field(default_factory=tuple)
    foreign_keys: tuple[tuple[str, str, str], ...] = field(default_factory=tuple)

    def column(self, name: str) -> ColumnInfo:
        for c in self.columns:
            if c.name == name:
                return c
        raise CatalogueError(f"unknown column {name!r} in dataset {self.name!r}")


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


class Catalogue:
    def __init__(
        self,
        db_path: str | Path,
        metadata_path: str | Path,
        *,
        timeout_s: float = 1.0,
    ):
        self.db_path, self.timeout_s = Path(db_path).resolve(), timeout_s
        meta = yaml.safe_load(Path(metadata_path).read_text()) or {}
        self._meta = meta.get("datasets", {})
        self.datasets = self._discover()
        self._pii = {(d.name, c.name) for d in self.datasets.values() for c in d.columns if c.pii}
        names = list(self.datasets)
        self._index = BM25Index().fit(names, [self._document(self.datasets[n]) for n in names])

    # ---- discovery (runs once, at start-up, with a plain read-only connection)

    def _connect_plain(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)

    def _discover(self) -> dict[str, Dataset]:
        con = self._connect_plain()
        try:
            objects = con.execute(
                "SELECT name, type, sql FROM sqlite_master WHERE type IN ('table','view') "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            datasets = {}
            for name, kind, ddl in objects:
                documented = name in self._meta
                meta = self._meta.get(name, {})
                col_meta = meta.get("columns", {})
                columns = tuple(
                    ColumnInfo(
                        r[1],
                        r[2] or "TEXT",
                        str(col_meta.get(r[1], {}).get("description", "")),
                        # fail closed: columns of undocumented datasets are treated as PII
                        bool(col_meta.get(r[1], {}).get("pii", False)) or not documented,
                        bool(r[5]),
                    )
                    for r in con.execute(f"PRAGMA table_info({_quote(name)})")
                )
                fks = tuple(
                    (r[3], r[2], r[4])
                    for r in con.execute(f"PRAGMA foreign_key_list({_quote(name)})")
                )
                count = con.execute(f"SELECT COUNT(*) FROM {_quote(name)}").fetchone()[0]
                datasets[name] = Dataset(
                    name=name,
                    kind=kind,
                    description=str(meta.get("description", "Undocumented dataset.")),
                    domain=str(meta.get("domain", "unclassified")),
                    owner=str(meta.get("owner", "unknown")),
                    tags=tuple(meta.get("tags", ())),
                    classification=str(meta.get("classification", "restricted")),  # fail closed
                    freshness_hours=meta.get("freshness_hours"),
                    columns=columns,
                    row_count=count,
                    upstream=self._upstream(ddl) if kind == "view" else (),
                    foreign_keys=fks,
                )
            return datasets
        finally:
            con.close()

    @staticmethod
    def _upstream(view_ddl: str) -> tuple[str, ...]:
        tree = sqlglot.parse_one(view_ddl, read="sqlite")
        query = tree.args.get("expression") or tree
        ctes = {c.alias for c in query.find_all(exp.CTE)}
        return tuple(sorted({t.name for t in query.find_all(exp.Table)} - ctes))

    @staticmethod
    def _document(d: Dataset) -> str:
        cols = " ".join(f"{c.name} {c.description}" for c in d.columns)
        return f"{d.name} {d.description} {d.domain} {' '.join(d.tags)} {cols}"

    # ---- bounded, read-only access

    def _guarded(self, dataset: Dataset) -> sqlite3.Connection:
        allowed = {dataset.name, *dataset.upstream}
        pii = self._pii

        def authorizer(action, arg1, arg2, _db, _src):
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ:
                ok = arg1 in allowed and (arg1, arg2) not in pii
                # a view's own columns inherit PII flags from the catalogue entry
                return sqlite3.SQLITE_OK if ok else sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _SAFE_FUNCTIONS:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        con = self._connect_plain()
        con.execute("PRAGMA query_only = ON")
        con.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        con.set_authorizer(authorizer)
        deadline = time.monotonic() + self.timeout_s
        con.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        return con

    def get(self, name: str) -> Dataset:
        if not isinstance(name, str) or name not in self.datasets:
            raise CatalogueError(f"unknown dataset {name!r}")
        return self.datasets[name]

    # ---- operations exposed as tools

    def list_datasets(self, domain: str | None = None, tag: str | None = None) -> list[dict]:
        return [
            {
                "name": d.name,
                "kind": d.kind,
                "domain": d.domain,
                "description": d.description,
                "tags": list(d.tags),
                "classification": d.classification,
            }
            for d in self.datasets.values()
            if (domain is None or d.domain == domain) and (tag is None or tag in d.tags)
        ]

    def search(self, query: str, limit: int = 5) -> list[dict]:
        if not 1 <= limit <= 20:
            raise CatalogueError("limit must be between 1 and 20")
        hits = self._index.search(query, limit)
        return [
            {
                "name": n,
                "score": round(s, 3),
                "kind": self.datasets[n].kind,
                "domain": self.datasets[n].domain,
                "description": self.datasets[n].description,
            }
            for n, s in hits
        ]

    def schema(self, name: str) -> dict:
        d = self.get(name)
        return {
            "name": d.name,
            "kind": d.kind,
            "description": d.description,
            "domain": d.domain,
            "owner": d.owner,
            "tags": list(d.tags),
            "classification": d.classification,
            "freshness_hours": d.freshness_hours,
            "row_count": d.row_count,
            "columns": [
                {
                    "name": c.name,
                    "type": c.type,
                    "description": c.description,
                    "pii": c.pii,
                    "primary_key": c.primary_key,
                }
                for c in d.columns
            ],
            "foreign_keys": [{"column": c, "references": f"{t}.{r}"} for c, t, r in d.foreign_keys],
        }

    def sample(self, name: str, rows: int = 5) -> dict:
        d = self.get(name)
        if not 1 <= rows <= MAX_SAMPLE_ROWS:
            raise CatalogueError(f"rows must be between 1 and {MAX_SAMPLE_ROWS}")
        readable = [c.name for c in d.columns if not c.pii]
        if not readable:
            raise CatalogueError(f"dataset {name!r} has no non-PII columns to sample")
        sql = f"SELECT {', '.join(map(_quote, readable))} FROM {_quote(d.name)} LIMIT {int(rows)}"
        con = self._guarded(d)
        try:
            fetched = con.execute(sql).fetchall()
        except sqlite3.DatabaseError as exc:
            raise CatalogueError(f"sample failed: {exc}") from exc
        finally:
            con.close()
        out = []
        for row in fetched:
            values = dict(zip(readable, row, strict=True))
            out.append({c.name: values.get(c.name, REDACTED) for c in d.columns})
        return {
            "dataset": d.name,
            "rows": out,
            "redacted_columns": [c.name for c in d.columns if c.pii],
        }

    def profile(self, name: str, column: str) -> dict:
        d = self.get(name)
        col = d.column(column)
        if col.pii:
            raise CatalogueError(f"column {column!r} is PII and cannot be profiled")
        q_col, q_tab = _quote(col.name), _quote(d.name)
        con = self._guarded(d)
        try:
            n, nulls, distinct, lo, hi = con.execute(
                f"SELECT COUNT(*), COUNT(*) - COUNT({q_col}), COUNT(DISTINCT {q_col}), "
                f"MIN({q_col}), MAX({q_col}) FROM {q_tab}"
            ).fetchone()
            top = []
            if 0 < distinct <= 50:  # only low-cardinality columns; never free text
                top = [
                    {"value": v, "count": c}
                    for v, c in con.execute(
                        f"SELECT {q_col}, COUNT(*) AS n FROM {q_tab} WHERE {q_col} IS NOT NULL "
                        f"GROUP BY {q_col} ORDER BY n DESC, {q_col} LIMIT {MAX_TOP_VALUES}"
                    )
                ]
        except sqlite3.DatabaseError as exc:
            raise CatalogueError(f"profile failed: {exc}") from exc
        finally:
            con.close()
        return {
            "dataset": d.name,
            "column": col.name,
            "type": col.type,
            "rows": n,
            "null_count": nulls,
            "null_rate": round(nulls / n, 4) if n else None,
            "distinct": distinct,
            "min": lo,
            "max": hi,
            "top_values": top,
        }

    def lineage(self, name: str) -> dict:
        d = self.get(name)
        downstream = sorted(x.name for x in self.datasets.values() if d.name in x.upstream)
        return {
            "dataset": d.name,
            "kind": d.kind,
            "upstream": list(d.upstream),
            "downstream": downstream,
        }
