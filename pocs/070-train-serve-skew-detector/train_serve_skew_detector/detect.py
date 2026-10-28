"""Compare offline vs online feature values for the same (entity, timestamp) and diagnose the kind of skew."""

from __future__ import annotations

import numpy as np

from .features import DAY, compute


def _num(x):
    return np.array(x, dtype=float)


def compare(off: dict, on: dict, queries, ev: dict, rtol: float = 1e-6) -> dict[str, dict]:
    """Per-feature report: mismatch rate and a diagnosed cause (or None)."""
    report = {}
    for f in off:
        a, b = off[f], on[f]
        if f == "country":
            mism = np.array([x != y for x, y in zip(a, b, strict=True)])
            ci_equal = all(x.lower() == y.lower() for x, y in zip(a, b, strict=True))
            cause = (
                "category_case"
                if mism.any() and ci_equal
                else ("category_vocab" if mism.any() else None)
            )
        elif f == "is_premium":
            mism = np.array([x != y for x, y in zip(a, b, strict=True)])
            cause = "type_or_label_mismatch" if mism.any() else None
        else:
            x, y = _num(a), _num(b)
            both_nan = np.isnan(x) & np.isnan(y)
            mism = ~both_nan & ~np.isclose(x, y, rtol=rtol, atol=1e-9, equal_nan=False)
            cause = None
            if mism.any():
                xn, yn = x[mism], y[mism]
                null_vs_zero = np.isnan(xn) & (yn == 0)
                if null_vs_zero.mean() > 0.9:
                    cause = "null_default"
                else:
                    ok = ~np.isnan(xn) & ~np.isnan(yn) & (xn != 0)
                    ratio = yn[ok] / xn[ok]
                    if (
                        ok.sum()
                        and np.std(ratio) < 1e-6 * max(abs(np.mean(ratio)), 1)
                        and abs(np.mean(ratio) - 1) > 1e-3
                    ):
                        cause = f"unit_scaling(x{np.mean(ratio):g})"
                    elif (
                        np.issubdtype(np.array(b).dtype, np.integer)
                        and (np.all(yn - xn >= 0) or np.all(yn - xn <= 0))
                        and np.max(np.abs(yn - xn)) <= 10
                    ):  # small one-directional integer drift = a shifted window boundary
                        cause = "window_off_by_one"
                    else:
                        cause = "unexplained_numeric_difference"
        report[f] = {"mismatch_rate": round(float(np.mean(mism)), 4), "cause": cause}
    return report


def stale_check(off_q: dict, on_q: dict, ev, queries, feature="avg_order_value_30d") -> float:
    """Share of mismatching rows where the online value equals the offline value of the previous day."""
    prev_q = [(u, t - DAY) for u, t in queries]
    prev = compute(ev, prev_q, "offline")[feature]
    x, y, p = _num(off_q[feature]), _num(on_q[feature]), _num(prev)
    mism = ~np.isclose(x, y, equal_nan=True)
    if not mism.any():
        return 0.0
    return float(
        np.mean(
            np.isclose(y[mism], p[mism], equal_nan=True) | (np.isnan(y[mism]) & np.isnan(p[mism]))
        )
    )


def leakage_check(ev, queries, stored: dict, rtol: float = 1e-6) -> dict[str, float]:
    """Recompute training features strictly as-of the timestamp; a stored value that differs used future data."""
    asof = compute(ev, queries, "offline")
    out = {}
    for f in ("avg_order_value_30d", "n_orders_7d"):
        x, y = _num(stored[f]), _num(asof[f])
        out[f] = round(float(np.mean(~np.isclose(x, y, rtol=rtol, equal_nan=True))), 4)
    return out
