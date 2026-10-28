"""Scoring against labelled anomalies, root-cause attribution and month-end forecasting."""

from __future__ import annotations

import numpy as np

from .generate import Anomaly


def score(found: list[tuple[int, int]], truth: list[Anomaly], tol: int = 2, warm: int = 28) -> dict:
    """An incident is a true positive if it overlaps a labelled anomaly window (+/- tol days)."""
    windows = [
        (a.start - tol, a.start + a.length - 1 + tol) for a in truth if a.start >= warm - tol
    ]
    tp_inc = sum(any(s <= we and e >= ws for ws, we in windows) for s, e in found)
    caught = []
    delay = []
    for a, (ws, we) in zip([a for a in truth if a.start >= warm - tol], windows, strict=True):
        hit = [s for s, e in found if s <= we and e >= ws]
        caught.append(bool(hit))
        if hit:
            delay.append(max(0, min(hit) - a.start))
    prec = tp_inc / len(found) if found else 1.0
    rec = sum(caught) / len(caught) if caught else 1.0
    return {"precision": round(prec, 3), "recall": round(rec, 3),
            "f1": round(2 * prec * rec / (prec + rec), 3) if prec + rec else 0.0,
            "incidents": len(found), "median_delay_days": float(np.median(delay)) if delay else None,
            "caught": caught}  # fmt: skip


def attribute(series: dict, day: int, lookback: int = 28) -> dict:
    """Which (service, account) explains the day's excess over its own weekday median?"""
    contrib = {}
    for key, v in series.items():
        past = np.array([v[j] for j in range(day - 7, max(day - 7 * 8, -1), -7)])
        contrib[key] = float(v[day] - np.median(past)) if len(past) else float(v[day])
    ranked = sorted(contrib.items(), key=lambda kv: -kv[1])
    top = ranked[0]
    return {"top": top[0], "excess_usd": round(top[1], 2), "share_of_excess": round(top[1] / max(sum(max(c, 0) for c in contrib.values()), 1e-9), 3),
            "by_service": max(by_excess(contrib, 0).items(), key=lambda kv: kv[1])[0]}  # fmt: skip


def by_excess(contrib: dict, idx: int) -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in contrib.items():
        out[k[idx]] = out.get(k[idx], 0.0) + max(v, 0.0)
    return out


def forecast_month_end(daily: np.ndarray, day_in_month: int, month_len: int = 30) -> float:
    """Month-to-date actual + remaining days at the recent weekday-adjusted run-rate."""
    mtd = float(daily[-day_in_month:].sum()) if day_in_month else 0.0
    recent = daily[-14:]
    return mtd + float(recent.mean()) * (month_len - day_in_month)


def budget_breach(
    daily: np.ndarray, budget: float, month_start: int, month_len: int = 30
) -> int | None:
    """First day of the month on which the forecast crosses the budget (None if never)."""
    for d in range(1, month_len):
        if forecast_month_end(daily[: month_start + d], d, month_len) > budget:
            return d
    return None
