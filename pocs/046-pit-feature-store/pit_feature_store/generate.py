"""Synthetic customer history where an after-the-fact feature leaks the label.

Daily snapshots of `tickets_7d` (support tickets in the last 7 days) and `orders_30d`. Churners
file many tickets in the days AFTER they churn (cancellation follow-ups), so a join that uses the
newest snapshot sees the label. A point-in-time join sees only what was known at churn time, where
the signal is weak (a small pre-churn drop in orders).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

START = datetime(2026, 1, 1)
DAYS = 90


def generate(n: int = 600, seed: int = 21) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    churn = rng.random(n) < 0.35
    label_day = rng.integers(30, 70, n)  # event time for every customer (churners: churn day)
    snaps = []
    for i in range(n):
        base = rng.poisson(8)
        for d in range(DAYS):
            after = churn[i] and d > label_day[i]
            before_drop = churn[i] and label_day[i] - 10 < d <= label_day[i]
            orders = max(0, int(rng.poisson(base * (0.6 if before_drop else 1.0))))
            tickets = int(rng.poisson(6 if after else 0.7))
            snaps.append((i, START + timedelta(days=d, hours=2), orders, tickets))
    feats = pd.DataFrame(snaps, columns=["customer_id", "feature_ts", "orders_30d", "tickets_7d"])
    labels = pd.DataFrame(
        {
            "entity_id": range(n),
            "event_ts": [START + timedelta(days=int(d), hours=12) for d in label_day],
            "churned": churn.astype(int),
        }
    )
    return feats, labels
