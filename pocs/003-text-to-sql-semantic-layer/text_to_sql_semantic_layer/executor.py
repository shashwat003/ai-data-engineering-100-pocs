"""Read-only SQL execution (layer 2 of 2), enforced by SQLite itself.

Independent of the validator: file opened with mode=ro, PRAGMA query_only, an authorizer that
denies everything except SELECT/READ of allowed columns and allowlisted functions, a wall-clock
timeout via the progress handler, a row cap, and SQLite size limits. Run per call on a fresh
connection so no state survives between queries.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .guard import Policy
from .schema import SchemaInfo, connect_readonly

# Engine-level names (what SQLite reports to the authorizer), including operator functions.
SQLITE_FUNCTIONS = frozenset(
    "count sum avg min max round abs coalesce ifnull nullif lower upper length substr substring "
    "trim ltrim rtrim strftime date like glob".split()
)


class ExecutionError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # "denied" | "timeout" | "error"


@dataclass
class ExecResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    elapsed_ms: float


class SafeExecutor:
    def __init__(
        self,
        db_path: str | Path,
        schema: SchemaInfo,
        policy: Policy | None = None,
        *,
        max_rows: int = 1000,
        timeout_s: float = 2.0,
    ):
        self.db_path, self.max_rows, self.timeout_s = Path(db_path), max_rows, timeout_s
        self.policy = policy or Policy()
        self._tables = {t.lower() for t in schema.tables}
        self._functions = SQLITE_FUNCTIONS

    def _authorizer(self, action, arg1, arg2, _db, _source):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table, column = (arg1 or "").lower(), (arg2 or "").lower()
            if table in self._tables and (table, column) not in self.policy.blocked_columns:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in self._functions:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY  # DDL, DML, PRAGMA, ATTACH, TRANSACTION, recursive CTE, ...

    def run(self, sql: str, params: tuple = ()) -> ExecResult:
        con = connect_readonly(self.db_path)
        try:
            con.execute("PRAGMA query_only = ON")
            con.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, self.policy.max_sql_chars * 2)
            con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
            con.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
            con.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 5)
            con.set_authorizer(self._authorizer)
            deadline = time.monotonic() + self.timeout_s
            con.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
            start = time.perf_counter()
            try:
                cur = con.execute(sql, params)
                fetched = cur.fetchmany(self.max_rows + 1)
            except sqlite3.DatabaseError as exc:
                msg = str(exc)
                if "not authorized" in msg or "readonly" in msg or "prohibited" in msg:
                    raise ExecutionError("denied", msg) from exc
                if "interrupted" in msg:
                    raise ExecutionError("timeout", f"exceeded {self.timeout_s}s") from exc
                raise ExecutionError("error", msg) from exc
            except (sqlite3.Warning, sqlite3.ProgrammingError) as exc:  # e.g. multiple statements
                raise ExecutionError("error", str(exc)) from exc
            return ExecResult(
                [d[0] for d in cur.description or []],
                fetched[: self.max_rows],
                len(fetched) > self.max_rows,
                (time.perf_counter() - start) * 1000,
            )
        finally:
            con.close()
