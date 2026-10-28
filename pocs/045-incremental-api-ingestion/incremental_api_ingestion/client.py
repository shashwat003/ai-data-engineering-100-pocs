"""Incremental sync client: watermark, retries with backoff, checkpoint-per-page, idempotent upsert."""

from __future__ import annotations

import json
import random
import sqlite3
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

KNOWN = {"id", "updated_at", "amount", "status", "deleted"}


class FatalApiError(Exception):
    """Non-retryable (auth, malformed request)."""


class RetriesExhausted(Exception):
    pass


@dataclass
class Stats:
    requests: int = 0
    retries: int = 0
    rate_limited: int = 0
    pages: int = 0
    rows_seen: int = 0
    slept: float = 0.0


class Syncer:
    def __init__(self, base_url: str, token: str, db: sqlite3.Connection, *, overlap_ticks: int = 0, page_size: int = 50,
                 max_retries: int = 5, sleep: Callable[[float], None] = time.sleep, timeout: float = 5.0, seed: int = 0):  # fmt: skip
        self.base, self.token, self.db = base_url, token, db
        self.overlap, self.page_size, self.max_retries = overlap_ticks, page_size, max_retries
        self.sleep, self.timeout, self.rng = sleep, timeout, random.Random(seed)
        self.stats = Stats()
        db.executescript("""
        CREATE TABLE IF NOT EXISTS target (id INTEGER PRIMARY KEY, updated_at INTEGER NOT NULL, amount REAL,
            status TEXT, deleted INTEGER NOT NULL, extra TEXT);
        CREATE TABLE IF NOT EXISTS sync_state (k TEXT PRIMARY KEY, v TEXT);""")

    # ---- state
    def watermark(self) -> int:
        row = self.db.execute("SELECT v FROM sync_state WHERE k='watermark'").fetchone()
        return int(row[0]) if row else 0

    # ---- http with retries
    def _get(self, path: str) -> dict:
        delay = 0.5
        for attempt in range(self.max_retries + 1):
            self.stats.requests += 1
            req = urllib.request.Request(
                self.base + path, headers={"Authorization": f"Bearer {self.token}"}
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 400, 404):
                    raise FatalApiError(f"HTTP {e.code}") from e
                if e.code == 429:
                    self.stats.rate_limited += 1
                    wait = float(e.headers.get("Retry-After", delay))
                else:
                    wait = delay * (
                        1 + self.rng.random() * 0.25
                    )  # jitter avoids synchronised retries
                    delay = min(delay * 2, 30)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                wait = delay * (1 + self.rng.random() * 0.25)
                delay = min(delay * 2, 30)
            if attempt == self.max_retries:
                raise RetriesExhausted(path)
            self.stats.retries += 1
            self.stats.slept += wait
            self.sleep(wait)
        raise RetriesExhausted(path)  # pragma: no cover

    # ---- sync
    def run(self, *, crash_after_pages: int | None = None) -> Stats:
        since = max(0, self.watermark() - self.overlap)
        cursor = self._resume_cursor(since)
        while True:
            body = self._get(
                f"/v1/items?updated_since={since}&limit={self.page_size}&cursor={cursor or 0}"
            )
            self._commit_page(body["data"], body["next_cursor"], since)
            self.stats.pages += 1
            if crash_after_pages is not None and self.stats.pages >= crash_after_pages:
                raise KeyboardInterrupt("simulated crash")
            if body["next_cursor"] is None:
                break
            cursor = body["next_cursor"]
        return self.stats

    def _resume_cursor(self, since: int) -> str | None:
        row = self.db.execute("SELECT v FROM sync_state WHERE k='cursor'").fetchone()
        sn = self.db.execute("SELECT v FROM sync_state WHERE k='cursor_since'").fetchone()
        return row[0] if row and sn and int(sn[0]) == since else None

    def _commit_page(self, rows: list[dict], next_cursor: str | None, since: int) -> None:
        """Upsert rows and move the checkpoint in ONE transaction: a crash can never persist one without the other."""
        with self.db:
            for r in rows:
                extra = {k: v for k, v in r.items() if k not in KNOWN}
                self.db.execute(
                    "INSERT INTO target (id, updated_at, amount, status, deleted, extra) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, amount=excluded.amount, "
                    "status=excluded.status, deleted=excluded.deleted, extra=excluded.extra WHERE excluded.updated_at >= target.updated_at",
                    (
                        r["id"],
                        r["updated_at"],
                        r.get("amount"),
                        r.get("status"),
                        int(r.get("deleted", False)),
                        json.dumps(extra) if extra else None,
                    ),
                )
            self.stats.rows_seen += len(rows)
            if next_cursor is not None:
                self.db.execute(
                    "INSERT OR REPLACE INTO sync_state VALUES ('cursor', ?)", (next_cursor,)
                )
                self.db.execute(
                    "INSERT OR REPLACE INTO sync_state VALUES ('cursor_since', ?)", (str(since),)
                )
            else:  # run finished: advance the watermark to the newest change seen and clear the cursor
                newest = self.db.execute(
                    "SELECT COALESCE(MAX(updated_at), 0) FROM target"
                ).fetchone()[0]
                self.db.execute(
                    "INSERT OR REPLACE INTO sync_state VALUES ('watermark', ?)", (str(newest),)
                )
                self.db.execute("DELETE FROM sync_state WHERE k IN ('cursor','cursor_since')")
