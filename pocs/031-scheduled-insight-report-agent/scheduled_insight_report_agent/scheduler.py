"""Idempotent scheduled report runs: cron ticks, catch-up of missed ticks, ledger, retrying delivery."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from .cron import Cron
from .facts import build_facts
from .narrative import SimWriter, render
from .verify import check

MAX_ATTEMPTS = 3
MAX_CATCHUP = 5


class FileOutbox:
    """Delivers to files named by idempotency key, written atomically; re-sending overwrites, never duplicates."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def send(self, key: str, subject: str, body: str) -> None:
        tmp = self.dir / f".{key}.tmp"
        tmp.write_text(f"Subject: {subject}\nIdempotency-Key: {key}\n\n{body}\n")
        tmp.replace(self.dir / f"{key}.txt")

    def sent(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.txt"))


class Ledger:
    def __init__(self, path: str | Path = ":memory:"):
        self.db = sqlite3.connect(str(path))
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS runs (report TEXT, period TEXT, status TEXT, attempts INTEGER, source TEXT, checksum TEXT, PRIMARY KEY (report, period))"
        )

    def get(self, report: str, period: str):
        return self.db.execute(
            "SELECT status, attempts FROM runs WHERE report=? AND period=?", (report, period)
        ).fetchone()

    def put(self, report, period, status, attempts, source="", checksum=""):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?)",
                (report, period, status, attempts, source, checksum),
            )

    def delivered(self, report: str) -> list[str]:
        return [
            r[0]
            for r in self.db.execute(
                "SELECT period FROM runs WHERE report=? AND status='delivered' ORDER BY period",
                (report,),
            )
        ]


def build_report(rows, end: date, writer: SimWriter | None = None) -> tuple[str, str, dict]:
    """Return (text, source, check). Writer output is used only if it passes verification; else the template text."""
    facts = build_facts(rows, end)
    base = render(facts)
    if writer is None:
        return base, "template", check(base, facts)
    candidate = writer.rewrite(base, facts)
    c = check(candidate, facts)
    return (candidate, "writer", c) if c["ok"] else (base, "template_fallback", c)


def run_due(
    report: str,
    cron: Cron,
    now: datetime,
    last_tick: datetime,
    rows,
    sink,
    ledger: Ledger,
    writer=None,
) -> list[dict]:
    """Process every tick in (last_tick, now], oldest first, at most MAX_CATCHUP; report covers the 7 days before the tick."""
    out = []
    for tick in cron.ticks(last_tick, now)[-MAX_CATCHUP:]:
        end = tick.date() - timedelta(days=1)
        period = end.isoformat()
        prev = ledger.get(report, period)
        if prev and prev[0] == "delivered":
            out.append({"period": period, "action": "already_delivered"})
            continue
        attempts = prev[1] if prev else 0
        text, source, chk = build_report(rows, end, writer)
        key = hashlib.sha256(f"{report}:{period}".encode()).hexdigest()[:16]
        while attempts < MAX_ATTEMPTS:
            attempts += 1
            try:
                sink.send(key, f"{report} {period}", text)
            except Exception as e:  # noqa: BLE001 (any sink failure is retried, never raised into the scheduler loop)
                ledger.put(
                    report,
                    period,
                    "failed" if attempts >= MAX_ATTEMPTS else "pending",
                    attempts,
                    source,
                )
                out.append(
                    {
                        "period": period,
                        "action": "send_failed",
                        "attempt": attempts,
                        "error": type(e).__name__,
                    }
                )
                continue
            ledger.put(
                report,
                period,
                "delivered",
                attempts,
                source,
                hashlib.sha256(text.encode()).hexdigest()[:12],
            )
            out.append(
                {
                    "period": period,
                    "action": "delivered",
                    "attempts": attempts,
                    "source": source,
                    "verified": chk["ok"],
                }
            )
            break
    return out
