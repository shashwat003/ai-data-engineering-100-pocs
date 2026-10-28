from __future__ import annotations

import numpy as np

from .hierarchy import H, generate
from .reconcile import METHODS, base_forecasts, coherence_gap, reconcile


def rmsse(actual, fc, train) -> np.ndarray:
    scale = np.mean(np.diff(train, axis=1) ** 2, axis=1)
    return np.sqrt(np.mean((actual - fc) ** 2, axis=1) / scale)


def run(
    n_obs: int = 120, origins: int = 6, seed: int = 5, n_regions: int = 3, stores: int = 4
) -> dict:
    allser, S, names = generate(n_obs, n_regions, stores, seed)
    m = S.shape[1]
    levels = {
        "total": [0],
        "region": list(range(1, 1 + n_regions)),
        "store": list(range(1 + n_regions, S.shape[0])),
    }
    err = {meth: {lv: [] for lv in levels} for meth in METHODS}
    gaps = {meth: [] for meth in METHODS}
    for k in range(origins):
        cut = n_obs - H - (origins - 1 - k) * 4
        train, actual = allser[:, :cut], allser[:, cut : cut + H]
        base, resid = base_forecasts(train)
        for meth in METHODS:
            f = reconcile(S, base, meth, resid=resid, bottom_hist=train[-m:])
            f = np.maximum(f, 0.0) if meth != "base (incoherent)" else f
            gaps[meth].append(coherence_gap(S, base if meth == "base (incoherent)" else f))
            e = rmsse(actual, f, train)
            for lv, idx in levels.items():
                err[meth][lv].append(float(e[idx].mean()))
    out = {
        "series": S.shape[0],
        "bottom_series": m,
        "origins": origins,
        "horizon": H,
        "methods": {},
    }
    for meth in METHODS:
        per = {lv: round(float(np.mean(v)), 3) for lv, v in err[meth].items()}
        per["all_levels_mean"] = round(float(np.mean(list(per.values()))), 3)
        out["methods"][meth] = {
            "rmsse_by_level": per,
            "max_coherence_gap": float(np.max(gaps[meth])),
        }
    base_mean = out["methods"]["base (incoherent)"]["rmsse_by_level"]["all_levels_mean"]
    best = min(
        (n for n in METHODS if n != "base (incoherent)"),
        key=lambda n: out["methods"][n]["rmsse_by_level"]["all_levels_mean"],
    )
    out["best_reconciled"] = best
    out["best_vs_base_pct"] = round(
        100 * (out["methods"][best]["rmsse_by_level"]["all_levels_mean"] / base_mean - 1), 1
    )
    return out


def multi_seed(seeds=range(1, 7), n_obs: int = 60, origins: int = 3) -> dict:
    """Repeat the experiment on different random hierarchies: reconciliation gains are small and vary by draw, so report spread."""
    rel = {m: [] for m in METHODS if m != "base (incoherent)"}
    for s in seeds:
        r = run(n_obs=n_obs, origins=origins, seed=s)
        base = r["methods"]["base (incoherent)"]["rmsse_by_level"]["all_levels_mean"]
        for m in rel:
            rel[m].append(100 * (r["methods"][m]["rmsse_by_level"]["all_levels_mean"] / base - 1))
    out = {}
    rng = np.random.default_rng(0)
    for m, v in rel.items():
        v = np.array(v)
        boots = [v[rng.integers(0, len(v), len(v))].mean() for _ in range(500)]
        out[m] = {
            "mean_change_pct": round(float(v.mean()), 2),
            "ci95": [
                round(float(np.percentile(boots, 2.5)), 2),
                round(float(np.percentile(boots, 97.5)), 2),
            ],
            "better_than_base_in": f"{int((v < 0).sum())}/{len(v)}",
        }
    return {"seeds": list(seeds), "n_obs": n_obs, "origins": origins, "methods": out}
