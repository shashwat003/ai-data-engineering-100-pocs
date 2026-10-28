"""Append-only, hash-chained audit log in SQLite. Tampering with any row breaks `verify()`."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from .redact import redact

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, con: sqlite3.Connection, now: Callable[[], str] | None = None):
        self.con = con
        self._now = now or (lambda: datetime.now(UTC).isoformat(timespec="seconds"))
        con.executescript("""
        CREATE TABLE IF NOT EXISTS audit_log (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, run_id TEXT NOT NULL,
            event TEXT NOT NULL, payload TEXT NOT NULL, prev_hash TEXT NOT NULL,
            hash TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
            BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
            BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
        """)

    @staticmethod
    def _digest(prev: str, ts: str, run_id: str, event: str, payload: str) -> str:
        blob = json.dumps([prev, ts, run_id, event, payload], separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def append(self, run_id: str, event: str, payload: dict) -> int:
        text = redact(json.dumps(payload, sort_keys=True, default=str))  # never log secrets/PII
        row = self.con.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        prev, ts = (row[0] if row else GENESIS), self._now()
        cur = self.con.execute(
            "INSERT INTO audit_log (ts, run_id, event, payload, prev_hash, hash) VALUES (?,?,?,?,?,?)",
            (ts, run_id, event, text, prev, self._digest(prev, ts, run_id, event, text)),
        )
        self.con.commit()
        return cur.lastrowid

    def events(self, run_id: str | None = None) -> list[dict]:
        sql = "SELECT seq, ts, run_id, event, payload FROM audit_log"
        rows = self.con.execute(sql + (" WHERE run_id=?" if run_id else "") + " ORDER BY seq",
                                (run_id,) if run_id else ())  # fmt: skip
        return [
            {"seq": s, "ts": t, "run_id": r, "event": e, "payload": json.loads(p)}
            for s, t, r, e, p in rows
        ]

    def verify(self) -> tuple[bool, int | None]:
        """Recompute the chain; return (ok, first_bad_seq)."""
        prev = GENESIS
        for seq, ts, run_id, event, payload, p, h in self.con.execute(
            "SELECT seq, ts, run_id, event, payload, prev_hash, hash FROM audit_log ORDER BY seq"
        ):
            if p != prev or h != self._digest(prev, ts, run_id, event, payload):
                return False, seq
            prev = h
        return True, None
