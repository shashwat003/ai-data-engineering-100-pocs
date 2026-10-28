"""Synthetic metric streams (5-minute points, daily seasonality) with labelled anomaly events."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

PERIOD = 288  # points per day


@dataclass(frozen=True)
class Event:
    kind: str
    start: int
    end: int  # inclusive


def base_signal(rng: random.Random, n: int) -> list[float]:
    out = []
    for i in range(n):
        season = (
            100 + 30 * math.sin(2 * math.pi * i / PERIOD) + 8 * math.sin(4 * math.pi * i / PERIOD)
        )
        out.append(season + rng.gauss(0, 4))
    return out


def make_stream(
    seed: int, n: int = 4 * PERIOD * 3, anomalies: bool = True
) -> tuple[list[float | None], list[Event]]:
    """Return values (None = missing) and the injected events. The first two days are always clean."""
    rng = random.Random(seed)
    x: list[float | None] = list(base_signal(rng, n))
    events: list[Event] = []
    if not anomalies:
        return x, events
    slots = list(range(2 * PERIOD + 50, n - 100, 200))
    rng.shuffle(slots)
    kinds = ["spike", "level_shift", "variance", "dropout", "seasonal_break"]
    for kind, s in zip(kinds * 3, slots, strict=False):
        if kind == "spike":
            x[s] += rng.choice([-1, 1]) * 55
            events.append(Event(kind, s, s))
        elif kind == "level_shift":
            for i in range(s, s + 60):
                x[i] += 28
            events.append(Event(kind, s, s + 59))
        elif kind == "variance":
            for i in range(s, s + 40):
                x[i] += rng.gauss(0, 22)
            events.append(Event(kind, s, s + 39))
        elif kind == "dropout":
            for i in range(s, s + 12):
                x[i] = None
            events.append(Event(kind, s, s + 11))
        else:  # the daily pattern flattens for a few hours: values stay in range but the shape is wrong
            mid = sum(v for v in x[s : s + 60] if v is not None) / 60
            for i in range(s, s + 60):
                x[i] = mid + rng.gauss(0, 4)
            events.append(Event(kind, s, s + 59))
    return x, sorted(events, key=lambda e: e.start)
