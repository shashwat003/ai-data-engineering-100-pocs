"""Three synthesizers written from scratch (no SDV): independent marginals, Gaussian copula, memoriser.

The copula can optionally build its marginals from Laplace-noised histograms (epsilon-DP for the
marginals only; the correlation matrix is NOT privatised, so no end-to-end DP claim is made).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


class Marginal:
    """Empirical (or DP-histogram) marginal with CDF-transform and inverse."""

    def __init__(self, s: pd.Series, numeric: bool, eps: float | None, rng, bins: int = 40):
        self.numeric = numeric
        if numeric:
            lo, hi = float(s.min()), float(s.max())
            self.edges = np.linspace(lo, hi, bins + 1)
            counts = np.histogram(s, bins=self.edges)[0].astype(float)
            self.integer = bool((s == s.round()).all())
        else:
            self.cats = sorted(s.unique())
            counts = np.array([(s == c).sum() for c in self.cats], dtype=float)
        if eps is not None:  # add/remove-one neighbouring datasets: sensitivity of a histogram is 1
            counts = np.clip(counts + rng.laplace(0, 1.0 / eps, len(counts)), 0, None)
        if counts.sum() == 0:
            counts = np.ones_like(counts)
        self.p = counts / counts.sum()
        self.cum = np.concatenate([[0.0], np.cumsum(self.p)])

    def to_u(self, s: pd.Series, rng) -> np.ndarray:
        """Map values to (0,1) via the marginal CDF (randomised within a bin/category)."""
        if self.numeric:
            idx = np.clip(
                np.searchsorted(self.edges, s.to_numpy(), side="right") - 1, 0, len(self.p) - 1
            )
            width = self.edges[idx + 1] - self.edges[idx]
            frac = np.where(
                width > 0, (s.to_numpy() - self.edges[idx]) / np.where(width > 0, width, 1), 0.5
            )
        else:
            idx = np.array([self.cats.index(v) for v in s])
            frac = rng.random(len(s))
        u = self.cum[idx] + np.clip(frac, 0, 1) * self.p[idx]
        return np.clip(u, 1e-6, 1 - 1e-6)

    def from_u(self, u: np.ndarray, rng) -> np.ndarray:
        idx = np.clip(np.searchsorted(self.cum, u, side="right") - 1, 0, len(self.p) - 1)
        if self.numeric:
            frac = np.where(
                self.p[idx] > 0,
                (u - self.cum[idx]) / np.where(self.p[idx] > 0, self.p[idx], 1),
                0.5,
            )
            x = self.edges[idx] + np.clip(frac, 0, 1) * (self.edges[idx + 1] - self.edges[idx])
            return np.round(x) if self.integer else np.round(x, 2)
        return np.array(self.cats, dtype=object)[idx]


class Synth:
    """kind: 'independent' | 'copula' | 'memoriser'."""

    def __init__(
        self,
        kind: str,
        num: list[str],
        cat: list[str],
        label: str,
        eps: float | None = None,
        seed: int = 0,
    ):
        if kind not in {"independent", "copula", "memoriser"}:
            raise ValueError(f"unknown synthesizer {kind!r}")
        if eps is not None and eps <= 0:
            raise ValueError("epsilon must be positive")
        self.kind, self.num, self.cat, self.label, self.eps = kind, num, cat, label, eps
        self.seed = seed

    @property
    def cols(self) -> list[str]:
        return self.num + self.cat + [self.label]

    def fit(self, df: pd.DataFrame) -> Synth:
        missing = set(self.cols) - set(df.columns)
        if missing:
            raise ValueError(f"missing columns {sorted(missing)}")
        if len(df) < 20:
            raise ValueError("need at least 20 rows")
        rng = np.random.default_rng(self.seed)
        self.train = df[self.cols].reset_index(drop=True)
        if self.kind == "memoriser":
            return self
        self.m = {c: Marginal(df[c], c in self.num, self.eps, rng) for c in self.cols}
        if self.kind == "copula":
            z = np.column_stack([stats.norm.ppf(self.m[c].to_u(df[c], rng)) for c in self.cols])
            self.corr = np.corrcoef(z, rowvar=False)
            w, v = np.linalg.eigh(self.corr)  # repair to PSD if numerical noise made it indefinite
            self.corr = (v * np.clip(w, 1e-6, None)) @ v.T
            d = np.sqrt(np.diag(self.corr))
            self.corr = self.corr / np.outer(d, d)
        return self

    def sample(self, n: int, seed: int | None = None) -> pd.DataFrame:
        rng = np.random.default_rng(self.seed + 1 if seed is None else seed)
        if self.kind == "memoriser":
            out = self.train.sample(
                n, replace=True, random_state=int(rng.integers(1 << 31))
            ).reset_index(drop=True)
            for c in self.num:  # tiny jitter: looks "different" but is a near-copy
                out[c] = (out[c] + rng.normal(0, 0.01 * self.train[c].std(), n)).round(2)
            return out
        k = len(self.cols)
        if self.kind == "copula":
            z = rng.multivariate_normal(np.zeros(k), self.corr, size=n)
            u = stats.norm.cdf(z)
        else:
            u = rng.random((n, k))
        out = pd.DataFrame({c: self.m[c].from_u(u[:, j], rng) for j, c in enumerate(self.cols)})
        out[self.label] = out[self.label].astype(int)
        return out
