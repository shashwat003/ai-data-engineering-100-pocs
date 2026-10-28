"""Synthetic monthly series from known generating processes, so each pattern's winner can be explained."""

from __future__ import annotations

import numpy as np

H = 12
PERIOD = 12
PATTERNS = [
    "trend_seasonal",
    "stable_seasonal",
    "changing_seasonality",
    "level_shift",
    "random_walk",
    "intermittent",
]


def make_series(pattern: str, rng: np.random.Generator, n: int) -> np.ndarray:
    t = np.arange(n)
    season = np.sin(2 * np.pi * t / PERIOD + rng.uniform(0, 6.28))
    if pattern == "trend_seasonal":
        y = 100 + rng.uniform(0.3, 1.2) * t + rng.uniform(8, 20) * season + rng.normal(0, 4, n)
    elif pattern == "stable_seasonal":
        y = 120 + rng.uniform(15, 35) * season + rng.normal(0, 5, n)
    elif pattern == "changing_seasonality":
        amp = np.linspace(rng.uniform(5, 10), rng.uniform(25, 40), n)
        y = 110 + 0.2 * t + amp * season + rng.normal(0, 4, n)
    elif pattern == "level_shift":
        y = (
            100
            + 6 * season
            + rng.normal(0, 3, n)
            + np.where(t > n - rng.integers(14, 30), rng.choice([-1, 1]) * rng.uniform(15, 30), 0)
        )
    elif pattern == "random_walk":
        y = 100 + np.cumsum(rng.normal(0.1, 3.0, n))
    else:  # intermittent demand: many zeros, occasional orders
        y = rng.poisson(0.35, n) * rng.integers(5, 25, n).astype(float)
    return np.maximum(y, 0.0)


def dataset(
    n_per_pattern: int = 10, seed: int = 7, min_len: int = 72, max_len: int = 108
) -> list[dict]:
    rng = np.random.default_rng(seed)
    out = []
    for p in PATTERNS:
        for i in range(n_per_pattern):
            n = int(rng.integers(min_len, max_len + 1))
            out.append({"id": f"{p}-{i:02d}", "pattern": p, "y": make_series(p, rng, n + H)})
    return out
