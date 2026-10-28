"""Drift statistics implemented with numpy and math only (no scipy), each checked against known values."""

from __future__ import annotations

import math

import numpy as np

EPS = 1e-4


def psi_numeric(ref: np.ndarray, cur: np.ndarray, bins: int = 10) -> float:
    """Population Stability Index with reference-quantile bins (open-ended outer bins)."""
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)[1:-1]))
    r = np.bincount(np.searchsorted(edges, ref, side="right"), minlength=len(edges) + 1) / len(ref)
    c = np.bincount(np.searchsorted(edges, cur, side="right"), minlength=len(edges) + 1) / len(cur)
    r, c = np.clip(r, EPS, None), np.clip(c, EPS, None)
    return float(np.sum((c - r) * np.log(c / r)))


def psi_categorical(ref: np.ndarray, cur: np.ndarray) -> float:
    cats = np.union1d(np.unique(ref), np.unique(cur))
    r = np.array([(ref == k).mean() for k in cats])
    c = np.array([(cur == k).mean() for k in cats])
    r, c = np.clip(r, EPS, None), np.clip(c, EPS, None)
    return float(np.sum((c - r) * np.log(c / r)))


def ks_2samp(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Two-sample Kolmogorov-Smirnov statistic and asymptotic p-value."""
    a, b = np.sort(a), np.sort(b)
    grid = np.concatenate([a, b])
    d = float(
        np.max(
            np.abs(
                np.searchsorted(a, grid, side="right") / len(a)
                - np.searchsorted(b, grid, side="right") / len(b)
            )
        )
    )
    en = math.sqrt(len(a) * len(b) / (len(a) + len(b)))
    lam = (en + 0.12 + 0.11 / en) * d
    if lam < 0.2:  # the alternating series does not converge near 0; the survival value there is 1
        return d, 1.0
    p = 2 * sum((-1) ** (k - 1) * math.exp(-2 * (k * lam) ** 2) for k in range(1, 101))
    return d, float(min(1.0, max(0.0, p)))


def _gammainc_lower(a: float, x: float) -> float:
    """Regularised lower incomplete gamma P(a, x) (series for x < a+1, else continued fraction)."""
    if x <= 0:
        return 0.0
    if x < a + 1:
        term = total = 1.0 / a
        n = a
        for _ in range(500):
            n += 1
            term *= x / n
            total += term
            if abs(term) < abs(total) * 1e-14:
                break
        return total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    b, c, d = x + 1 - a, 1e300, 1 / (x + 1 - a)
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1 / d
        h *= d * c
        if abs(d * c - 1) < 1e-14:
            break
    return 1 - math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi2_sf(x: float, dof: int) -> float:
    return 1.0 - _gammainc_lower(dof / 2, x / 2)


def chi2_homogeneity(ref: np.ndarray, cur: np.ndarray) -> tuple[float, float]:
    """Chi-square test that two categorical samples share a distribution."""
    cats = np.union1d(np.unique(ref), np.unique(cur))
    table = np.array(
        [[np.sum(ref == k) for k in cats], [np.sum(cur == k) for k in cats]], dtype=float
    )
    expected = table.sum(1, keepdims=True) * table.sum(0, keepdims=True) / table.sum()
    mask = expected > 0
    stat = float(np.sum((table[mask] - expected[mask]) ** 2 / expected[mask]))
    dof = max(1, len(cats) - 1)
    return stat, chi2_sf(stat, dof)


def proportion_z(p_ref: float, n_ref: int, p_cur: float, n_cur: int) -> tuple[float, float]:
    """Two-proportion z-test (e.g. missing-value rate); returns (z, two-sided p)."""
    pooled = (p_ref * n_ref + p_cur * n_cur) / (n_ref + n_cur)
    se = math.sqrt(max(pooled * (1 - pooled) * (1 / n_ref + 1 / n_cur), 1e-12))
    z = (p_cur - p_ref) / se
    return z, math.erfc(abs(z) / math.sqrt(2))


def js_divergence(p: np.ndarray, q: np.ndarray) -> float:
    p, q = np.asarray(p, float) + 1e-12, np.asarray(q, float) + 1e-12
    p, q = p / p.sum(), q / q.sum()
    m = (p + q) / 2
    return float(0.5 * np.sum(p * np.log2(p / m)) + 0.5 * np.sum(q * np.log2(q / m)))
