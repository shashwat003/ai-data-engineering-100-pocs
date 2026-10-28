"""Anomaly detectors for a daily cost series. Each returns a boolean array of flagged days."""

from __future__ import annotations

import numpy as np


def dod_threshold(x: np.ndarray, pct: float = 0.30) -> np.ndarray:
    """Naive baseline: flag days more than `pct` above the previous day."""
    out = np.zeros(len(x), bool)
    out[1:] = x[1:] > x[:-1] * (1 + pct)
    return out


def rolling_z(x: np.ndarray, window: int = 28, z: float = 3.5) -> np.ndarray:
    out = np.zeros(len(x), bool)
    for i in range(window, len(x)):
        w = x[i - window : i]
        sd = w.std()
        out[i] = sd > 0 and (x[i] - w.mean()) / sd > z
    return out


def seasonal_mad(
    x: np.ndarray, window: int = 8, k: float = 5.0, min_rel: float = 0.10
) -> np.ndarray:
    """Compare each day with the median of the same weekday over the previous `window` weeks, scaled by
    that weekday history's MAD; also require a minimum relative excess to ignore tiny noisy series."""
    out = np.zeros(len(x), bool)
    for i in range(7 * 3, len(x)):
        hist = x[i - 7 :: -7][:window] if i >= 7 else np.array([])
        hist = np.array([x[j] for j in range(i - 7, -1, -7)][:window])
        med = np.median(hist)
        mad = np.median(np.abs(hist - med)) * 1.4826 + 1e-9
        out[i] = (x[i] - med) / mad > k and x[i] > med * (1 + min_rel)
    return out


def ewma(x: np.ndarray, lam: float = 0.2, L: float = 5.0, warm: int = 28) -> np.ndarray:
    """EWMA control chart on the residual from a weekday profile estimated from the warm-up period."""
    prof = np.array([np.median(x[d:warm:7]) for d in range(7)])
    resid = x / prof[np.arange(len(x)) % 7] - 1
    mu, sd = resid[:warm].mean(), resid[:warm].std() + 1e-9
    s = mu
    out = np.zeros(len(x), bool)
    limit = L * sd * np.sqrt(lam / (2 - lam))
    for i in range(len(x)):
        s = lam * resid[i] + (1 - lam) * s
        out[i] = i >= warm and s - mu > limit
    return out


DETECTORS = {
    "day_over_day_30pct": dod_threshold,
    "rolling_z": rolling_z,
    "weekday_mad": seasonal_mad,
    "ewma": ewma,
}


def incidents(flags: np.ndarray, gap: int = 2) -> list[tuple[int, int]]:
    """Merge flagged days separated by at most `gap` days into (start, end) incidents."""
    days = np.flatnonzero(flags)
    out: list[list[int]] = []
    for d in days:
        if out and d - out[-1][1] - 1 <= gap:  # `gap` = quiet days allowed between flags
            out[-1][1] = int(d)
        else:
            out.append([int(d), int(d)])
    return [(a, b) for a, b in out]
