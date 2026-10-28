"""Synthetic clickstream: event-time sessions per user, arrival order with delays, exact oracle."""

from __future__ import annotations

import json
import os
import random
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

T0 = datetime(2025, 3, 1, 8, 0, tzinfo=UTC)
GAP = timedelta(minutes=30)


def make_events(n_users: int = 30, seed: int = 4, hours: int = 5) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for u in range(n_users):
        t = T0 + timedelta(minutes=rng.randint(0, 40))
        end = T0 + timedelta(hours=hours)
        while t < end:
            for _ in range(rng.randint(2, 9)):  # a burst of activity = one session
                out.append(
                    {
                        "user": f"u{u:02d}",
                        "ts": t,
                        "page": rng.choice(["home", "item", "cart", "search"]),
                    }
                )
                t += timedelta(
                    seconds=rng.randint(10, 600)
                )  # always < 30 min apart inside a session
            t += timedelta(minutes=rng.randint(31, 120))  # idle gap that closes the session
    out.sort(key=lambda e: (e["ts"], e["user"]))
    return out


def oracle_sessions(events: list[dict], gap: timedelta = GAP) -> list[tuple]:
    """Batch sessionisation: a new session starts when the gap to the previous event is >= gap.
    Returns (user, start, end=last_ts+gap, count) tuples, matching Spark's session_window bounds."""
    by_user: dict[str, list[datetime]] = {}
    for e in events:
        by_user.setdefault(e["user"], []).append(e["ts"])
    res = []
    for u, ts in by_user.items():
        ts.sort()
        start = prev = ts[0]
        n = 1
        for t in ts[1:]:
            if t - prev >= gap:
                res.append((u, start, prev + gap, n))
                start, n = t, 0
            prev = t
            n += 1
        res.append((u, start, prev + gap, n))
    return sorted(res)


def assign_arrival(
    events: list[dict],
    n_batches: int = 12,
    late_frac: float = 0.06,
    very_late_frac: float = 0.01,
    seed: int = 9,
) -> list[list[dict]]:
    """Arrival time = event time + delay; most events are prompt, some late, a few very late. Returns micro-batches."""
    rng = random.Random(seed)
    arr = []
    for e in events:
        r = rng.random()
        delay = timedelta(minutes=rng.uniform(0, 3))
        if r < very_late_frac:
            delay = timedelta(hours=rng.uniform(2, 3))
        elif r < very_late_frac + late_frac:
            delay = timedelta(minutes=rng.uniform(10, 50))
        arr.append((e["ts"] + delay, e))
    arr.sort(key=lambda x: x[0])
    size = -(-len(arr) // n_batches)
    return [[e for _, e in arr[i : i + size]] for i in range(0, len(arr), size)]


def write_batches(batches: list[list[dict]], directory: str | Path, flush: bool = True) -> Path:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    base = time.time() - 3600
    paths = []  # Spark's file source orders files by modification time, so make the order explicit and strictly increasing
    for i, b in enumerate(batches):
        paths.append(d / f"batch_{i:03d}.json")
        paths[-1].write_text(
            "\n".join(
                json.dumps({"user": e["user"], "ts": e["ts"].isoformat(), "page": e["page"]})
                for e in b
            )
            + "\n"
        )
    if (
        flush
    ):  # a sentinel far in the future advances the watermark so every real session is finalised
        far = T0 + timedelta(days=30)
        paths.append(d / f"batch_{len(batches):03d}_flush.json")
        paths[-1].write_text(
            json.dumps({"user": "__flush__", "ts": far.isoformat(), "page": "x"}) + "\n"
        )
    for i, p in enumerate(paths):
        os.utime(p, (base + i * 60, base + i * 60))
    return d


def simulate_drops(
    batches: list[list[dict]], watermark: timedelta, gap: timedelta = GAP, rule: str = "window_end"
) -> int:
    """Independent model of Spark's rule: the watermark used for batch k is max event time seen in batches < k minus the
    delay; an event is dropped if its session window (ts, ts + gap) already ends at or before that watermark."""
    seen_max, dropped = None, 0
    for b in batches:
        wm = None if seen_max is None else seen_max - watermark
        for e in b:
            if wm is not None and (
                (e["ts"] + gap <= wm) if rule == "window_end" else (e["ts"] < wm)
            ):
                dropped += 1
        batch_max = max(e["ts"] for e in b)
        seen_max = batch_max if seen_max is None else max(seen_max, batch_max)
    return dropped
