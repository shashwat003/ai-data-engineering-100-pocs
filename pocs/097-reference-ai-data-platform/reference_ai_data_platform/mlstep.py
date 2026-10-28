"""Order-level return-risk model on top of the gold layer, promoted through POC 065's registry gate.

The synthetic orders carry no learnable signal (cross-validated AUC about 0.5 against the stored status), so the
LABEL here is SIMULATED from a documented rule applied to real gold features. Rule v2 (from the fourth landing
batch) flips the sign of two effects: concept drift the champion cannot know about.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
from mlflow_registry_promotion.registry import (
    Gate,
    evaluate_gate,
    promote,
    train_and_register,
)

from pocs_common.rand import stable_unit

FEATURES = ("amount_k", "enterprise", "smb", "prior_orders", "weekend", "us")
DRIFT_FROM_WINDOW = 4


def window_bounds(con, k: int) -> tuple[date, date]:
    effs = [
        r[0]
        for r in con.execute(
            "SELECT effective_date FROM ledger WHERE entity='orders' ORDER BY batch_id"
        ).fetchall()
    ]
    return (date(1900, 1, 1) if k == 1 else effs[k - 2]), effs[k - 1]


def window_rows(con, k: int):
    """Real gold features for the orders of window k: (order_ids, X, group)."""
    lo, hi = window_bounds(con, k)
    rows = con.execute(
        """
        SELECT order_id, amount, segment_at_order, country_at_order, dayofweek(order_date),
               count(*) OVER (PARTITION BY customer_sk ORDER BY order_date, order_id
                              ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior
        FROM gold_fct_orders ORDER BY order_id""",
    ).fetchall()
    dates = dict(con.execute("SELECT order_id, order_date FROM gold_fct_orders").fetchall())
    keep = [r for r in rows if lo <= dates[r[0]] < hi]
    ids = np.array([r[0] for r in keep])
    X = np.array(
        [
            [
                float(r[1]) / 1000,
                r[2] == "enterprise",
                r[2] == "smb",
                float(r[5]),
                r[4] in (0, 6),
                r[3] == "United States",
            ]
            for r in keep
        ],
        dtype=float,
    )
    group = np.array([r[2] == "consumer" for r in keep], dtype=int)
    return ids, X, group


def simulated_label(ids: np.ndarray, X: np.ndarray, window: int, seed: int = 0) -> np.ndarray:
    amount, ent, smb, prior, weekend, us = (X[:, i] for i in range(6))
    s = -1.0 if window >= DRIFT_FROM_WINDOW else 1.0
    logit = (
        -0.4
        + s * 1.6 * (amount - 0.75)
        + 0.9 * ent
        - 0.6 * smb
        + s * 0.25 * (prior - 3)
        + 0.4 * weekend
    )
    p = 1 / (1 + np.exp(-logit))
    u = np.array([stable_unit(seed, int(i)) for i in ids])
    return (u < p).astype(int)


@dataclass
class ModelOutcome:
    window: int
    candidate: str
    promoted: bool
    reasons: list[str]
    auc_delta_ci: tuple[float, float]
    champion: str | None


def split_folds(ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    train = ids % 2 == 0
    return train, ~train


def train_and_gate(
    client, con, window: int, seed: int = 0, *, broken_candidate: bool = False
) -> list[ModelOutcome]:
    """Train on the even-id half of window `window`, gate on the odd-id half (never seen in training)."""
    ids, X, group = window_rows(con, window)
    y = simulated_label(ids, X, window, seed)
    tr, ho = split_folds(ids)
    outcomes = []
    candidates = [("regular", dict(C=1.0, drop_feature=None))]
    if broken_candidate:
        candidates.append(("missing feature", dict(C=1.0, drop_feature=5)))
    for name, kw in candidates:
        v = train_and_register(
            client, X[tr], y[tr], seed=seed, note=f"window {window}: {name}", **kw
        )
        d = evaluate_gate(client, v, X[ho], y[ho], group[ho], Gate(), seed)
        try:
            champ = client.get_model_version_by_alias("churn-model", "champion").version
        except Exception:
            champ = None
        if d.promote:
            promote(client, v, "platform", "; ".join(d.reasons))
        outcomes.append(
            ModelOutcome(window, f"v{v} ({name})", d.promote, d.reasons, d.delta_ci, champ)
        )
    return outcomes
