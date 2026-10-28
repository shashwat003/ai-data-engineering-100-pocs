from __future__ import annotations

import time

import numpy as np

from .models import STAT_MODELS, GlobalLGBM
from .series import PATTERNS, PERIOD, H, dataset


def smape(a, f):
    d = np.abs(a) + np.abs(f)
    return float(np.mean(np.where(d == 0, 0.0, 2 * np.abs(a - f) / np.where(d == 0, 1, d))) * 100)


def mase(a, f, train):
    scale = np.mean(np.abs(train[PERIOD:] - train[:-PERIOD]))
    return float(np.mean(np.abs(a - f)) / scale) if scale > 0 else float("nan")


def rolling_origins(y: np.ndarray, k: int = 2) -> list[int]:
    """Cut points: forecast the H points after each cut (the last H observations form the final test window)."""
    return [len(y) - H - i * 6 for i in reversed(range(k))]


def run(n_per_pattern: int = 8, seed: int = 7, origins: int = 2) -> dict:
    data = dataset(n_per_pattern, seed)
    names = [*STAT_MODELS, "LightGBM global", "combo: ETS+Theta+LightGBM"]
    rows = {n: [] for n in names}
    meta = []
    secs = {n: 0.0 for n in names}
    for cut_rank in range(origins):
        cuts = {s["id"]: rolling_origins(s["y"], origins)[cut_rank] for s in data}
        lgb = GlobalLGBM().fit(
            [s["y"][: cuts[s["id"]]] for s in data]
        )  # trained only on data before each series' cut
        for s in data:
            c = cuts[s["id"]]
            train, actual = s["y"][:c], s["y"][c : c + H]
            f = {}
            for n, fn in STAT_MODELS.items():
                t0 = time.perf_counter()
                f[n] = fn(train)
                secs[n] += time.perf_counter() - t0
            t0 = time.perf_counter()
            f["LightGBM global"] = lgb.forecast(train)
            secs["LightGBM global"] += time.perf_counter() - t0
            f["combo: ETS+Theta+LightGBM"] = (
                f["ETS (AICc)"] + f["Theta (simplified)"] + f["LightGBM global"]
            ) / 3
            meta.append(s["pattern"])
            for n in names:
                rows[n].append((smape(actual, f[n]), mase(actual, f[n], train)))
    out = {"n_series": len(data), "origins": origins, "horizon": H, "models": {}}
    pats = np.array(meta)
    for n in names:
        a = np.array(rows[n])
        out["models"][n] = {"smape": round(float(np.nanmean(a[:, 0])), 2), "mase": round(float(np.nanmean(a[:, 1])), 3), "seconds_per_forecast": round(secs.get(n, 0) / len(meta), 4) if n in secs and secs[n] else None,
                            "mase_by_pattern": {p: round(float(np.nanmean(a[pats == p, 1])), 2) for p in PATTERNS}}  # fmt: skip
    # paired bootstrap: is the best model really better than seasonal naive?
    rng = np.random.default_rng(0)
    base = np.array(rows["seasonal naive"])[:, 1]
    best = min(names, key=lambda n: out["models"][n]["mase"])
    diff = np.array(rows[best])[:, 1] - base
    boots = [np.nanmean(diff[rng.integers(0, len(diff), len(diff))]) for _ in range(500)]
    out["best_model"] = best
    out["best_vs_seasonal_naive_mase_diff_ci95"] = [
        round(float(np.percentile(boots, 2.5)), 3),
        round(float(np.percentile(boots, 97.5)), 3),
    ]
    ranks = np.array([[rows[n][i][1] for n in names] for i in range(len(meta))])
    ranks = np.argsort(np.argsort(np.nan_to_num(ranks, nan=1e9), axis=1), axis=1) + 1
    out["mean_rank"] = {n: round(float(ranks[:, j].mean()), 2) for j, n in enumerate(names)}
    out["foundation_models"] = (
        "not run: no pretrained weights or network access in this environment (adapter interface only)"
    )
    return out
