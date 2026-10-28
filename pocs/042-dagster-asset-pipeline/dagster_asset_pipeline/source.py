"""Deterministic synthetic event source per day, with duplicates, bad rows, and a scripted outage."""

from __future__ import annotations

import random
from datetime import date

OUTAGE_DAYS = {"2025-03-05"}  # the source returns nothing on these days until fixed


def events_for(day: str, outage: set[str] | None = None) -> list[dict]:
    if day in (OUTAGE_DAYS if outage is None else outage):
        return []
    rng = random.Random(day)
    n = rng.randint(80, 120)
    rows = [
        {"event_id": f"{day}-{i:04d}", "user_id": f"u{rng.randint(1, 40)}", "amount": round(rng.uniform(1, 90), 2), "event_date": day}
        for i in range(n)
    ]  # fmt: skip
    for r in rng.sample(rows, 5):  # exact duplicates delivered twice
        rows.append(dict(r))
    for r in rng.sample(rows[:n], 3):
        r["amount"] = -abs(r["amount"])  # invalid
    for r in rng.sample(rows[:n], 2):
        r["user_id"] = None  # invalid
    rng.shuffle(rows)
    return rows


def days(start: str, n: int) -> list[str]:
    d0 = date.fromisoformat(start)
    return [date.fromordinal(d0.toordinal() + i).isoformat() for i in range(n)]
