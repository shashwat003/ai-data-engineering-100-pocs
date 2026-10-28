"""Base forecasts and reconciliation: bottom-up, top-down, OLS, structural WLS, MinT with shrinkage. All are y~ = S P y^."""

from __future__ import annotations

import warnings

import numpy as np
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from .hierarchy import PERIOD, H

warnings.filterwarnings("ignore")


def base_forecast(y: np.ndarray, h: int = H) -> tuple[np.ndarray, np.ndarray]:
    """Damped-trend additive-seasonal Holt-Winters: returns (h-step forecast, in-sample one-step residuals)."""
    m = ExponentialSmoothing(
        y,
        trend="add",
        damped_trend=True,
        seasonal="add",
        seasonal_periods=PERIOD,
        initialization_method="estimated",
    ).fit()
    return np.maximum(m.forecast(h), 0.0), y - m.fittedvalues


def base_forecasts(all_series: np.ndarray, h: int = H) -> tuple[np.ndarray, np.ndarray]:
    f, r = zip(*(base_forecast(y, h) for y in all_series), strict=True)
    return np.array(f), np.array(r)  # [n_series, h], [n_series, n_obs]


def shrunk_covariance(resid: np.ndarray) -> np.ndarray:
    """Schaefer-Strimmer shrinkage of the residual covariance towards its diagonal (MinT-shrink, Wickramasuriya et al.)."""
    r = resid - resid.mean(axis=1, keepdims=True)
    n = r.shape[1]
    cov = r @ r.T / n
    d = np.sqrt(np.diag(cov))
    corr = cov / np.outer(d, d)
    off = ~np.eye(len(cov), dtype=bool)
    # variance of the sample correlations (plug-in estimate)
    z = r / d[:, None]
    var_corr = np.zeros_like(corr)
    for i in range(len(cov)):
        for j in range(len(cov)):
            if i != j:
                w = z[i] * z[j]
                var_corr[i, j] = w.var(ddof=1) * n / (n - 1) ** 2 if n > 2 else 0.0
    lam = (
        float(np.clip(var_corr[off].sum() / (corr[off] ** 2).sum(), 0.0, 1.0))
        if (corr[off] ** 2).sum() > 0
        else 1.0
    )
    shrunk = corr.copy()
    shrunk[off] *= 1 - lam
    return shrunk * np.outer(d, d), lam


def projection(
    S: np.ndarray,
    method: str,
    resid: np.ndarray | None = None,
    bottom_hist: np.ndarray | None = None,
) -> np.ndarray:
    """Return the matrix P (n_bottom x n_series) of the reconciliation y~ = S P y^."""
    n, m = S.shape
    if method == "bottom-up":
        return np.hstack([np.zeros((m, n - m)), np.eye(m)])
    if method == "top-down":
        if bottom_hist is None:
            raise ValueError("top-down needs bottom-level history")
        p = (
            bottom_hist.mean(axis=1) / bottom_hist.sum(axis=0).mean()
        )  # average historical proportions of the total
        return np.hstack([p[:, None], np.zeros((m, n - 1))])
    if method == "OLS":
        W = np.eye(n)
    elif method == "WLS (structural)":
        W = np.diag(S.sum(axis=1))
    elif method == "MinT (shrink)":
        if resid is None:
            raise ValueError("MinT needs in-sample residuals")
        W, _ = shrunk_covariance(resid)
    else:
        raise ValueError(f"unknown method {method!r}")
    Wi = np.linalg.pinv(W)
    return np.linalg.solve(S.T @ Wi @ S, S.T @ Wi)


METHODS = ["base (incoherent)", "bottom-up", "top-down", "OLS", "WLS (structural)", "MinT (shrink)"]


def reconcile(
    S: np.ndarray, base: np.ndarray, method: str, resid=None, bottom_hist=None
) -> np.ndarray:
    if method == "base (incoherent)":
        return base
    return S @ projection(S, method, resid, bottom_hist) @ base


def coherence_gap(S: np.ndarray, f: np.ndarray) -> float:
    """Largest violation of the aggregation constraints: how far each forecast is from S times its bottom level."""
    m = S.shape[1]
    return float(np.abs(f - S @ f[-m:]).max())
