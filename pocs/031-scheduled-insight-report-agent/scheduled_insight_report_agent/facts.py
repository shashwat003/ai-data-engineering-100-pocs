"""Compute the numbers a report may talk about. Everything in the narrative must trace back to these."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, pstdev

Z_THRESHOLD = 3.5


def _sum(rows, d0: date, d1: date, key: str, seg: str | None = None) -> float:
    return sum(
        r[key]
        for r in rows
        if d0 <= date.fromisoformat(r["date"]) <= d1 and (seg is None or r["segment"] == seg)
    )


def build_facts(rows: list[dict], end: date, days: int = 7) -> dict:
    """Metrics for the `days` ending at `end`, versus the previous period and a 4-period baseline."""
    start = end - timedelta(days=days - 1)
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    if min(date.fromisoformat(r["date"]) for r in rows) > prev_start - timedelta(days=days * 3):
        raise ValueError("not enough history for a 4-period baseline")
    cur_rev, prev_rev = (
        _sum(rows, start, end, "revenue"),
        _sum(rows, prev_start, prev_end, "revenue"),
    )
    cur_ord, prev_ord = _sum(rows, start, end, "orders"), _sum(rows, prev_start, prev_end, "orders")
    base = [
        _sum(rows, start - timedelta(days=days * k), end - timedelta(days=days * k), "revenue")
        for k in range(1, 5)
    ]
    daily = defaultdict(float)
    for r in rows:
        daily[r["date"]] += r["revenue"]
    hist = [v for k, v in daily.items() if date.fromisoformat(k) < start]

    # ratio of each day to the mean of the same weekday in the previous 4 weeks; the spread of that ratio is
    # estimated from ALL history before the window (pooled), because 4 reference points give a noisy per-day sigma
    def ratio(d: date):
        ref = [
            daily[(d - timedelta(days=7 * j)).isoformat()]
            for j in range(1, 5)
            if (d - timedelta(days=7 * j)).isoformat() in daily
        ]
        return daily[d.isoformat()] / mean(ref) if len(ref) == 4 else None

    hist_ratios = [
        r for k in daily if (d := date.fromisoformat(k)) < start and (r := ratio(d)) is not None
    ]
    sigma = pstdev(hist_ratios) if len(hist_ratios) >= 14 else None
    z = {}
    if sigma:
        for k in daily:
            d = date.fromisoformat(k)
            if start <= d <= end and (r := ratio(d)) is not None:
                z[k] = (r - 1) / sigma
    anomalies = sorted(
        ((k, round(zv, 1)) for k, zv in z.items() if abs(zv) >= Z_THRESHOLD), key=lambda x: x[0]
    )
    segs = {
        s: (_sum(rows, start, end, "revenue", s), _sum(rows, prev_start, prev_end, "revenue", s))
        for s in sorted({r["segment"] for r in rows})
    }
    movers = sorted(((s, c - p) for s, (c, p) in segs.items()), key=lambda x: -abs(x[1]))
    return {
        "period": f"{start.isoformat()} to {end.isoformat()}", "days": days,
        "revenue": round(cur_rev), "revenue_prev": round(prev_rev), "revenue_change_pct": round(100 * (cur_rev - prev_rev) / prev_rev, 1),
        "orders": int(cur_ord), "orders_prev": int(prev_ord), "orders_change_pct": round(100 * (cur_ord - prev_ord) / prev_ord, 1),
        "baseline_avg": round(mean(base)), "vs_baseline_pct": round(100 * (cur_rev - mean(base)) / mean(base), 1),
        "aov": round(cur_rev / cur_ord, 2), "anomalies": anomalies, "history_days": len(hist),
        "top_mover": movers[0][0], "top_mover_change": round(movers[0][1]), "segment_revenue": {s: round(c) for s, (c, _) in segs.items()},
    }  # fmt: skip
