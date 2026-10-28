"""Fairness audit: group metrics, gap checks, bootstrap CIs, proxy detection, mitigation."""

from __future__ import annotations

import numpy as np
import pandas as pd
from fairlearn.metrics import (
    MetricFrame,
    demographic_parity_difference,
    equalized_odds_difference,
    false_positive_rate,
    selection_rate,
    true_positive_rate,
)
from fairlearn.postprocessing import ThresholdOptimizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score

FOUR_FIFTHS = 0.8  # disparate-impact rule of thumb (a screening heuristic, not a legal test)


def group_report(y_true, y_pred, groups) -> dict:
    mf = MetricFrame(
        metrics={
            "selection_rate": selection_rate,
            "tpr": true_positive_rate,
            "fpr": false_positive_rate,
            "accuracy": accuracy_score,
        },
        y_true=y_true,
        y_pred=y_pred,
        sensitive_features=groups,
    )
    by = mf.by_group
    sr = by["selection_rate"]
    return {
        "by_group": {
            str(g): {k: round(float(v), 4) for k, v in row.items()} for g, row in by.iterrows()
        },
        "demographic_parity_diff": round(
            float(demographic_parity_difference(y_true, y_pred, sensitive_features=groups)), 4
        ),
        "equalized_odds_diff": round(
            float(equalized_odds_difference(y_true, y_pred, sensitive_features=groups)), 4
        ),
        "disparate_impact_ratio": round(float(sr.min() / sr.max()) if sr.max() > 0 else 1.0, 4),
        "overall_accuracy": round(float(mf.overall["accuracy"]), 4),
    }


def passes_four_fifths(report: dict) -> bool:
    return report["disparate_impact_ratio"] >= FOUR_FIFTHS


def bootstrap_ci(
    y_true, y_pred, groups, stat, n_boot: int = 200, seed: int = 0
) -> tuple[float, float]:
    """95% percentile CI of a disparity statistic; wide intervals mean 'do not over-read'."""
    rng = np.random.default_rng(seed)
    yt, yp, g = np.asarray(y_true), np.asarray(y_pred), np.asarray(groups)
    vals = []
    for _ in range(n_boot):
        i = rng.integers(0, len(yt), len(yt))
        if len(set(g[i])) < 2:
            continue
        vals.append(stat(yt[i], yp[i], g[i]))
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return round(float(lo), 4), round(float(hi), 4)


def dp_stat(yt, yp, g):
    return float(demographic_parity_difference(yt, yp, sensitive_features=g))


def proxy_scan(X: pd.DataFrame, groups) -> dict:
    """Which features predict the protected attribute? AUC of a one-feature model (0.5 = none)."""
    y = (np.asarray(groups) == sorted(set(groups))[-1]).astype(int)
    out = {}
    for c in X.columns:
        m = LogisticRegression().fit(X[[c]], y)
        out[c] = round(float(roc_auc_score(y, m.predict_proba(X[[c]])[:, 1])), 4)
    return out


def train_eval(df: pd.DataFrame, features, seed: int = 0, include_group: bool = False):
    """Fit on a split; return the fitted model, test frame and predictions."""
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(df))
    cut = int(len(df) * 0.7)
    tr, te = df.iloc[idx[:cut]], df.iloc[idx[cut:]]
    cols = list(features) + (["is_b"] if include_group else [])
    tr, te = (
        tr.assign(is_b=(tr.group == "B").astype(int)),
        te.assign(is_b=(te.group == "B").astype(int)),
    )
    model = LogisticRegression(max_iter=500).fit(tr[cols], tr.approved)
    return model, tr, te, cols


def mitigate(model, tr, te, cols, constraint: str = "demographic_parity", seed: int = 0):
    """Post-processing: group-specific thresholds (fairlearn ThresholdOptimizer)."""
    to = ThresholdOptimizer(
        estimator=model,
        constraints=constraint,
        objective="accuracy_score",
        predict_method="predict_proba",
        prefit=True,
    )
    to.fit(tr[cols], tr.approved, sensitive_features=tr.group)
    return to.predict(te[cols], sensitive_features=te.group, random_state=seed)
