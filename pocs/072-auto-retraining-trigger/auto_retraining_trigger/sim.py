"""52-week simulation of a deployed classifier under drift, with four retraining policies.

World: 5 features, weekly batches of 1,500 rows. The true decision rule rotates gradually, one weight
flips sign suddenly at week 20 (concept drift), and the inputs shift at week 35 (covariate drift).
Labels arrive 2 weeks late, so performance-based triggers see old news; drift detectors on inputs do not.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from drift_monitoring.detectors import ks_2samp
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

WEEKS, N, D, LABEL_DELAY = 52, 1500, 5, 2


def make_week(rng, t: int):
    shift = np.array([1.2, 0, 0, 0, 0]) if t >= 35 else np.zeros(D)
    X = rng.normal(0, 1, (N, D)) + shift
    w = np.array([1.5, -1.0, 0.6, 0.0, 0.3])
    w = w + 0.02 * t * np.array([0.5, 0.5, -0.5, 1.0, 0.0])  # gradual rotation
    if t >= 20:
        w[1] = -w[1]  # sudden concept flip
    y = (X @ w + rng.normal(0, 0.8, N) > 0).astype(int)
    return X, y


@dataclass
class Policy:
    name: str
    every: int | None = None  # fixed schedule (weeks)
    drift: bool = False  # KS drift on inputs
    perf: float | None = (
        None  # retrain when delayed AUC drops below this fraction of the post-train AUC
    )
    cooldown: int = 4
    max_per_quarter: int = 2
    gate: bool = (
        False  # only deploy if the new model beats the old one on the most recent labelled week
    )


def run(
    policy: Policy | None,
    seed: int,
    train_weeks: int = 4,
    corrupt_training_labels_from: int | None = None,
) -> dict:
    rng = np.random.default_rng(seed)
    weeks = [make_week(rng, t) for t in range(WEEKS)]
    train_end = train_weeks
    Xtr = np.vstack([weeks[t][0] for t in range(train_end)])
    ytr = np.concatenate([weeks[t][1] for t in range(train_end)])
    model = LogisticRegression(max_iter=300).fit(Xtr, ytr)
    Xtr[:, 0].copy()
    ref_X_all = Xtr
    base_auc = roc_auc_score(ytr, model.predict_proba(Xtr)[:, 1])
    last_train, retrains, rejected, aucs = train_end - 1, [], 0, []
    for t in range(train_end, WEEKS):
        X, y = weeks[t]
        aucs.append(
            float(roc_auc_score(y, model.predict_proba(X)[:, 1]))
        )  # serving quality this week (labels known later)
        if policy is None:
            continue
        fire = False
        if policy.every and (t - last_train) >= policy.every:
            fire = True
        if policy.drift:
            fire |= any(ks_2samp(ref_X_all[:, j], X[:, j])[1] < 0.001 / D for j in range(D))
        if policy.perf is not None and t - LABEL_DELAY > last_train + 1:
            Xo, yo = weeks[t - LABEL_DELAY]
            fire |= roc_auc_score(yo, model.predict_proba(Xo)[:, 1]) < policy.perf * base_auc
        recent = [r for r in retrains if t - r < 13]
        if fire and (t - last_train) >= policy.cooldown and len(recent) < policy.max_per_quarter:
            lo = max(
                0, t - LABEL_DELAY - 3
            )  # train on labelled weeks only (labels lag), window of 4 weeks
            Xn = np.vstack([weeks[k][0] for k in range(lo, t - LABEL_DELAY + 1)])
            yn = np.concatenate([weeks[k][1] for k in range(lo, t - LABEL_DELAY + 1)])
            if (
                corrupt_training_labels_from is not None and t >= corrupt_training_labels_from
            ):  # a broken label pipeline feeds training only
                yn = np.random.default_rng(seed * 1000 + t).integers(0, 2, len(yn))
            cand = LogisticRegression(max_iter=300).fit(Xn, yn)
            Xv, yv = weeks[t - LABEL_DELAY]
            better = roc_auc_score(yv, cand.predict_proba(Xv)[:, 1]) >= roc_auc_score(
                yv, model.predict_proba(Xv)[:, 1]
            )
            if policy.gate and not better:
                rejected += 1
                last_train = t  # restart the cooldown; the attempt still cost a training run
                retrains.append(t)
                continue
            model, last_train = cand, t
            ref_X_all = Xn
            base_auc = roc_auc_score(yn, cand.predict_proba(Xn)[:, 1])
            retrains.append(t)
    return {
        "mean_auc": float(np.mean(aucs)),
        "worst_auc": float(np.min(aucs)),
        "retrains": len(retrains),
        "rejected": rejected,
        "aucs": aucs,
        "retrain_weeks": retrains,
    }


POLICIES = [
    None,
    Policy("fixed every 8 weeks", every=8, cooldown=8, max_per_quarter=9),
    Policy("drift-triggered (KS on inputs)", drift=True),
    Policy("performance-triggered (labels lag 2 wk)", perf=0.92),
    Policy("drift OR performance, cooldown + budget + gate", drift=True, perf=0.92, gate=True),
]
