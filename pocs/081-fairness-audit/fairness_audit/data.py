"""Synthetic lending data with a known, planted bias mechanism.

Group B has the same underlying repayment ability as group A, but (1) a proxy feature
(`postcode_risk`) correlates with group membership and (2) historical labels under-approve
group B at equal ability (`label_bias`). The generating process is known, so the audit can be
checked against ground truth instead of just reported.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

GROUPS = ("A", "B")


def make_lending(
    n: int = 6000, seed: int = 7, proxy_strength: float = 2.0, label_bias: float = 2.0
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    group = rng.choice(GROUPS, size=n, p=[0.65, 0.35])
    is_b = (group == "B").astype(float)
    ability = rng.normal(0, 1, n)  # true repayment ability, identical distribution per group
    income = 50 + 12 * ability + rng.normal(0, 6, n)
    debt_ratio = np.clip(0.35 - 0.05 * ability + rng.normal(0, 0.08, n), 0.02, 0.95)
    postcode_risk = proxy_strength * is_b + rng.normal(0, 1, n)  # proxy: leaks group membership
    logit = 1.4 * ability - 0.9 * is_b * label_bias + 0.2
    approved = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)  # biased historical label
    return pd.DataFrame(
        {
            "income": income,
            "debt_ratio": debt_ratio,
            "postcode_risk": postcode_risk,
            "group": group,
            "approved": approved,
            "ability": ability,
        }
    )


FEATURES = ["income", "debt_ratio", "postcode_risk"]
