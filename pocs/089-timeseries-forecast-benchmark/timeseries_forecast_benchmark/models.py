"""Forecasters: baselines, statsmodels ETS / Theta-style / differenced ARMA, and a global LightGBM model."""

from __future__ import annotations

import warnings

import numpy as np
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from .series import PERIOD, H

warnings.filterwarnings("ignore")


def naive(y, h=H):
    return np.repeat(y[-1], h)


def seasonal_naive(y, h=H):
    return np.array([y[-PERIOD + (i % PERIOD)] for i in range(h)])


def drift(y, h=H):
    slope = (y[-1] - y[0]) / (len(y) - 1)
    return np.maximum(y[-1] + slope * np.arange(1, h + 1), 0.0)  # demand cannot be negative


def ets(y, h=H):
    """Best of a few Holt-Winters configurations by AICc (additive error)."""
    best, best_f = np.inf, None
    for trend, damped, seasonal in [
        (None, False, None),
        ("add", True, None),
        (None, False, "add"),
        ("add", True, "add"),
    ]:
        if seasonal and len(y) < 2 * PERIOD + 2:
            continue
        try:
            m = ExponentialSmoothing(
                y + 1e-6,
                trend=trend,
                damped_trend=damped,
                seasonal=seasonal,
                seasonal_periods=PERIOD if seasonal else None,
                initialization_method="estimated",
            ).fit()
            if m.aicc < best:
                best, best_f = m.aicc, m.forecast(h)
        except Exception:  # noqa: BLE001 (a failing configuration is skipped, not fatal)
            continue
    return np.maximum(best_f if best_f is not None else naive(y, h), 0)


def theta(y, h=H):
    """Simplified Theta-style forecaster: SES on a seasonally adjusted series plus half the fitted linear slope per step."""
    n = len(y)
    if n >= 2 * PERIOD:
        idx = np.array([y[i::PERIOD].mean() for i in range(PERIOD)])
        idx = idx / idx.mean() if idx.mean() > 0 else np.ones(PERIOD)
        idx = np.where(idx <= 0.05, 0.05, idx)
        adj = y / idx[np.arange(n) % PERIOD]
    else:
        idx, adj = np.ones(PERIOD), y
    m = ExponentialSmoothing(adj + 1e-6, initialization_method="estimated").fit()
    slope = np.polyfit(np.arange(n), adj, 1)[0] / 2
    f = m.forecast(h) + slope * np.arange(1, h + 1)
    return np.maximum(f * idx[(n + np.arange(h)) % PERIOD], 0)


def sarima_diff(y, h=H):
    """Seasonal difference at lag 12, then ARIMA(1,0,1) with a constant on the differences."""
    if len(y) < PERIOD + 20:
        return naive(y, h)
    d = y[PERIOD:] - y[:-PERIOD]
    try:
        f = ARIMA(d, order=(1, 0, 1), trend="c").fit().forecast(h)
    except Exception:  # noqa: BLE001
        return seasonal_naive(y, h)
    hist = list(y)
    out = []
    for i in range(h):
        out.append(hist[-PERIOD] + f[i])
        hist.append(out[-1])
    return np.maximum(np.array(out), 0)


def _features(y: np.ndarray, t: int, lags: int = 24) -> list[float]:
    window = y[t - lags : t]
    scale = (
        window[-PERIOD:].mean() + 1.0
    )  # +1 keeps all-zero stretches (intermittent demand) from exploding the ratios
    return [
        *np.clip(window / scale, 0, 10).tolist(),
        np.sin(2 * np.pi * t / PERIOD),
        np.cos(2 * np.pi * t / PERIOD),
        scale,
    ]


class GlobalLGBM:
    """One LightGBM model trained across all series (target and lags scaled by the recent mean); recursive multi-step."""

    name = "LightGBM global"

    def __init__(self, lags: int = 24):
        import lightgbm as lgb

        self.lags = lags
        self.model = lgb.LGBMRegressor(
            n_estimators=150,
            learning_rate=0.05,
            num_leaves=15,
            min_child_samples=10,
            subsample=0.8,
            subsample_freq=1,
            colsample_bytree=0.8,
            random_state=0,
            verbose=-1,
            n_jobs=1,
        )

    def fit(self, series: list[np.ndarray]):
        X, Y = [], []
        for y in series:
            for t in range(self.lags, len(y)):
                f = _features(y, t, self.lags)
                X.append(f)
                Y.append(min(10.0, y[t] / f[-1]))
        self.model.fit(np.array(X), np.array(Y))
        return self

    def forecast(self, y: np.ndarray, h: int = H) -> np.ndarray:
        hist = list(y)
        for _ in range(h):
            f = _features(np.array(hist), len(hist), self.lags)
            hist.append(max(0.0, float(self.model.predict(np.array([f]))[0]) * f[-1]))
        return np.array(hist[-h:])


STAT_MODELS = {
    "naive": naive,
    "seasonal naive": seasonal_naive,
    "drift": drift,
    "ETS (AICc)": ets,
    "Theta (simplified)": theta,
    "seasonal-diff ARMA": sarima_diff,
}
