"""Churn-risk model and uplift learners (S, T, X) on scikit-learn gradient boosting; no causal-inference library."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

from .data import FEATURES


def _clf(seed=0):
    return HistGradientBoostingClassifier(
        max_iter=120, learning_rate=0.08, max_depth=4, random_state=seed
    )


def _reg(seed=0):
    return HistGradientBoostingRegressor(
        max_iter=100, learning_rate=0.08, max_depth=3, random_state=seed
    )


class ChurnRisk:
    """Naive targeting model: probability of churn, ignoring the treatment."""

    def fit(self, df: pd.DataFrame):
        self.m = _clf().fit(df[FEATURES], df["churned"])
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:  # higher = riskier
        return self.m.predict_proba(df[FEATURES])[:, 1]


class SLearner:
    def fit(self, df: pd.DataFrame):
        self.m = _clf().fit(df[[*FEATURES, "treated"]], df["churned"])
        return self

    def score(
        self, df: pd.DataFrame
    ) -> np.ndarray:  # predicted reduction in churn probability caused by the offer
        x0, x1 = df[FEATURES].assign(treated=0), df[FEATURES].assign(treated=1)
        return self.m.predict_proba(x0)[:, 1] - self.m.predict_proba(x1)[:, 1]


class TLearner:
    def fit(self, df: pd.DataFrame):
        t, c = df[df.treated == 1], df[df.treated == 0]
        self.m1, self.m0 = (
            _clf(1).fit(t[FEATURES], t["churned"]),
            _clf(2).fit(c[FEATURES], c["churned"]),
        )
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        return self.m0.predict_proba(df[FEATURES])[:, 1] - self.m1.predict_proba(df[FEATURES])[:, 1]


class XLearner:
    """Kuenzel et al. X-learner with the known randomisation propensity (0.5 here, estimated from the data)."""

    def fit(self, df: pd.DataFrame):
        t, c = df[df.treated == 1], df[df.treated == 0]
        self.m1, self.m0 = (
            _clf(1).fit(t[FEATURES], t["churned"]),
            _clf(2).fit(c[FEATURES], c["churned"]),
        )
        # imputed individual effects (reduction in churn): control units use the treated model, treated units the control model
        d1 = self.m0.predict_proba(t[FEATURES])[:, 1] - t["churned"].to_numpy()
        d0 = c["churned"].to_numpy() - self.m1.predict_proba(c[FEATURES])[:, 1]
        self.t1, self.t0 = _reg(3).fit(t[FEATURES], d1), _reg(4).fit(c[FEATURES], d0)
        self.e = float(df["treated"].mean())
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        return (1 - self.e) * self.t1.predict(df[FEATURES]) + self.e * self.t0.predict(df[FEATURES])
