"""A 3-level sales hierarchy (total > region > store) with shared regional shocks, so reconciliation has structure to exploit."""

from __future__ import annotations

import numpy as np

H = 8
PERIOD = 12


def summing_matrix(n_regions: int = 3, stores_per_region: int = 4) -> tuple[np.ndarray, list[str]]:
    n_bottom = n_regions * stores_per_region
    rows = [np.ones(n_bottom)]
    names = ["total"]
    for r in range(n_regions):
        row = np.zeros(n_bottom)
        row[r * stores_per_region : (r + 1) * stores_per_region] = 1
        rows.append(row)
        names.append(f"region{r}")
    rows += list(np.eye(n_bottom))
    names += [f"store{r}-{s}" for r in range(n_regions) for s in range(stores_per_region)]
    return np.array(rows), names


def generate(
    n_obs: int = 96, n_regions: int = 3, stores_per_region: int = 4, seed: int = 5
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Return (all series, shape [n_series, n_obs], S, names). Bottom series have seasonality, trend and noise; regions share a shock."""
    rng = np.random.default_rng(seed)
    S, names = summing_matrix(n_regions, stores_per_region)
    n_bottom = S.shape[1]
    t = np.arange(n_obs)
    region_shock = np.cumsum(rng.normal(0, 3.0, (n_regions, n_obs)), axis=1) * 0.3 + rng.normal(
        0, 4.0, (n_regions, n_obs)
    )
    bottom = np.zeros((n_bottom, n_obs))
    for b in range(n_bottom):
        r = b // stores_per_region
        level = rng.uniform(40, 160)
        amp = level * rng.uniform(0.1, 0.35)
        bottom[b] = (
            level
            + rng.uniform(-0.1, 0.4) * t / 4
            + amp * np.sin(2 * np.pi * t / PERIOD + rng.uniform(0, 6.28))
            + region_shock[r] * (level / 100)
            + rng.normal(0, level * 0.07, n_obs)
        )
    bottom = np.maximum(bottom, 1.0)
    return S @ bottom, S, names
