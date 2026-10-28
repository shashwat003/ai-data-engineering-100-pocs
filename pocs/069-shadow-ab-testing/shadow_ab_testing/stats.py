"""Experiment statistics: SRM check, fixed-horizon tests, sequential alpha spending, CUPED, power."""

from __future__ import annotations

import math

import numpy as np
from scipy import stats as sps
from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import proportion_effectsize, proportions_ztest


def srm_pvalue(n_control: int, n_treatment: int, expected_share: float = 0.5) -> float:
    """Sample-ratio-mismatch chi-square p-value: tiny means the assignment itself is broken."""
    n = n_control + n_treatment
    exp = [n * (1 - expected_share), n * expected_share]
    return float(sps.chisquare([n_control, n_treatment], exp).pvalue)


def two_prop_p(x_c: int, n_c: int, x_t: int, n_t: int) -> float:
    return float(proportions_ztest([x_t, x_c], [n_t, n_c])[1])


def welch_p(a: np.ndarray, b: np.ndarray) -> float:
    return float(sps.ttest_ind(a, b, equal_var=False).pvalue)


def msprt_lambda(delta_hat: float, var_delta: float, tau2: float) -> float:
    """Mixture-SPRT likelihood ratio for H0: delta = 0 with a N(0, tau2) mixing prior (normal approximation).

    Rejecting when lambda >= 1/alpha at ANY look keeps the false-positive rate <= alpha under continuous
    monitoring (Johari et al.). var_delta is the variance of the estimated difference at this look.
    """
    return math.sqrt(var_delta / (var_delta + tau2)) * math.exp(
        tau2 * delta_hat**2 / (2 * var_delta * (var_delta + tau2))
    )


def cuped(y: np.ndarray, x_pre: np.ndarray) -> np.ndarray:
    """CUPED-adjusted outcome: y - theta * (x_pre - mean(x_pre)) with theta = cov/var estimated on the pooled sample."""
    theta = np.cov(x_pre, y)[0, 1] / np.var(x_pre, ddof=1)
    return y - theta * (x_pre - x_pre.mean())


def sample_size_per_arm(
    p_base: float, lift_abs: float, alpha: float = 0.05, power: float = 0.8
) -> int:
    es = proportion_effectsize(p_base + lift_abs, p_base)
    return int(math.ceil(NormalIndPower().solve_power(es, alpha=alpha, power=power, ratio=1.0)))
