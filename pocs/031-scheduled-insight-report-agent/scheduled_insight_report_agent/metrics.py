"""Synthetic daily business metrics with seasonality, a planted incident and a segment mix."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

SEGMENTS = ["consumer", "smb", "enterprise"]
INCIDENT_DAY = 100  # index of the planted revenue drop (consumer segment)


def daily(n_days: int = 120, seed: int = 3, start: date = date(2025, 1, 1)) -> list[dict]:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_days):
        d = start + timedelta(days=i)
        season = 1 + 0.25 * np.sin((d.weekday() - 4) / 7 * 2 * np.pi)  # weekday/weekend cycle
        trend = 1 + 0.002 * i
        for seg, base in zip(SEGMENTS, (6000, 4000, 9000), strict=True):
            mult = 0.55 if (seg == "consumer" and INCIDENT_DAY <= i < INCIDENT_DAY + 3) else 1.0
            rev = base * season * trend * mult * rng.normal(1, 0.04)
            orders = max(1, int(rev / rng.normal(52 if seg != "enterprise" else 310, 3)))
            rows.append(
                {
                    "date": d.isoformat(),
                    "segment": seg,
                    "revenue": round(float(rev), 2),
                    "orders": orders,
                }
            )
    return rows
