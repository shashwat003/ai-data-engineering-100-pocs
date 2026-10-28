"""Difference-in-differences on simulated panels: classic 2x2, parallel-trends test, staggered adoption.

Why staggered adoption needs care: two-way fixed effects (TWFE) with a single treatment dummy uses
already-treated units as controls for later-treated ones. When effects grow over time this "forbidden
comparison" biases the estimate, even its sign can flip. The group-time estimator below compares each
cohort only with never-treated units.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import statsmodels.api as sm  # used by classic_2x2


@dataclass
class Panel:
    y: np.ndarray  # (N, T)
    cohort: np.ndarray  # first treated period per unit; -1 = never
    true_att: float  # average true effect over treated unit-periods
    effect: np.ndarray  # (N, T) true effect

    @property
    def n(self) -> int:
        return self.y.shape[0]

    @property
    def t(self) -> int:
        return self.y.shape[1]

    def treated(self) -> np.ndarray:
        t = np.arange(self.t)[None, :]
        return ((self.cohort[:, None] >= 0) & (t >= self.cohort[:, None])).astype(float)


def simulate(n: int = 300, t: int = 20, cohorts: tuple[int, ...] = (8, 14), share_never: float = 0.34, effect: dict | None = None, dynamic: float = 0.6, trend_gap: float = 0.0,
             noise: float = 1.0, seed: int = 0) -> Panel:  # fmt: skip
    """effect[g] = base effect for cohort g; effect grows by `dynamic` per period since treatment.
    trend_gap adds a linear pre-existing trend to ever-treated units (violating parallel trends)."""
    rng = np.random.default_rng(seed)
    effect = effect or {8: 2.0, 14: 1.0}
    cohort = np.full(n, -1)
    k = len(cohorts)
    assign = rng.choice(k + 1, n, p=[(1 - share_never) / k] * k + [share_never])
    for i, a in enumerate(assign):
        cohort[i] = cohorts[a] if a < k else -1
    unit_fe, time_fe = rng.normal(0, 2, (n, 1)), rng.normal(0, 1, (1, t))
    ts = np.arange(t)[None, :]
    eff = np.zeros((n, t))
    for i in range(n):
        g = cohort[i]
        if g >= 0:
            eff[i, g:] = effect[g] + dynamic * (np.arange(g, t) - g)
    y = (
        unit_fe
        + time_fe
        + eff
        + trend_gap * (cohort[:, None] >= 0) * ts
        + rng.normal(0, noise, (n, t))
    )
    treated = (cohort[:, None] >= 0) & (ts >= cohort[:, None])
    return Panel(y, cohort, float(eff[treated].mean()), eff)


def _demean(m: np.ndarray) -> np.ndarray:
    """Two-way within transformation of a balanced (units x periods) matrix."""
    return m - m.mean(1, keepdims=True) - m.mean(0, keepdims=True) + m.mean()


def _fwl_cluster(y: np.ndarray, x: np.ndarray) -> tuple[float, float, float]:
    """Slope of y on x after two-way demeaning (Frisch-Waugh-Lovell) with unit-clustered CR1 standard errors."""
    n, t = y.shape
    yt, xt = _demean(y), _demean(x)
    sxx = float((xt * xt).sum())
    beta = float((xt * yt).sum() / sxx)
    e = yt - beta * xt
    score = (xt * e).sum(1)  # per-unit score
    k = n + t  # parameters absorbed by the fixed effects, approximately
    corr = n / (n - 1) * (n * t - 1) / (n * t - k)
    se = float(np.sqrt(corr * (score**2).sum()) / sxx)
    return beta, se, beta / se


def twfe(p: Panel) -> tuple[float, float]:
    """Two-way fixed effects y ~ D + unit FE + time FE (via within transformation) with clustered SEs."""
    b, se, _ = _fwl_cluster(p.y, p.treated())
    return b, se


def group_time_att(p: Panel) -> dict[tuple[int, int], float]:
    """ATT(g, t) = change in cohort g from g-1 to t minus the change in never-treated units."""
    never = p.cohort < 0
    out = {}
    for g in sorted(set(p.cohort[p.cohort >= 0])):
        m = p.cohort == g
        for t in range(p.t):
            if t == g - 1:
                continue
            out[(int(g), t)] = float(
                (p.y[m, t].mean() - p.y[m, g - 1].mean())
                - (p.y[never, t].mean() - p.y[never, g - 1].mean())
            )
    return out


def overall_att(p: Panel) -> float:
    """Average ATT(g,t) over post-treatment cells, each cohort weighted by its size and its number of periods."""
    att = group_time_att(p)
    num = den = 0.0
    for (g, t), v in att.items():
        if t >= g:
            w = float((p.cohort == g).sum())
            num += w * v
            den += w
    return num / den


def bootstrap_se(p: Panel, fn, reps: int = 100, seed: int = 0) -> float:
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(reps):
        idx = rng.integers(0, p.n, p.n)  # resample whole units (cluster bootstrap)
        vals.append(fn(Panel(p.y[idx], p.cohort[idx], p.true_att, p.effect[idx])))
    return float(np.std(vals, ddof=1))


def classic_2x2(p: Panel, g: int) -> tuple[float, float]:
    """One cohort vs never-treated, one pre and one post period (t=g-1 vs t=g+3): the textbook DiD."""
    keep = (p.cohort == g) | (p.cohort < 0)
    y0, y1 = p.y[keep, g - 1], p.y[keep, g + 3]
    treat = (p.cohort[keep] == g).astype(float)
    X = sm.add_constant(treat)
    res = sm.OLS(y1 - y0, X).fit(cov_type="HC1")
    return float(res.params[1]), float(res.bse[1])


def pretrend_test(p: Panel, g: int) -> float:
    """p-value of (cohort g) x (linear time) using only pre-treatment periods; small = parallel trends rejected."""
    from scipy import stats

    keep = (p.cohort == g) | (p.cohort < 0)
    sub = p.y[keep][:, :g]
    n, t = sub.shape
    x = (p.cohort[keep] == g).astype(float)[:, None] * np.arange(t)[None, :]
    _, _, z = _fwl_cluster(sub, x)
    return float(2 * (1 - stats.norm.cdf(abs(z))))
