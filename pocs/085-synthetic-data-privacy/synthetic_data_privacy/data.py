"""Synthetic "real" customer table with correlated numeric and categorical columns and a label."""

from __future__ import annotations

import numpy as np
import pandas as pd

NUM = ["age", "income", "tenure_months", "monthly_spend"]
CAT = ["region", "plan"]
LABEL = "churned"
REGIONS = ["north", "south", "east", "west"]
PLANS = ["basic", "plus", "pro"]


def make_real(n: int = 3000, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    age = np.clip(rng.normal(42, 13, n), 18, 85)
    income = np.exp(rng.normal(10.6 + 0.012 * (age - 42), 0.45, n))
    tenure = np.clip(rng.gamma(2.0, 14, n) * (0.6 + age / 100), 0, 240)
    plan = np.where(
        income > np.quantile(income, 0.8),
        "pro",
        np.where(income > np.quantile(income, 0.45), "plus", "basic"),
    )
    flip = rng.random(n) < 0.15
    plan = np.where(flip, rng.choice(PLANS, n), plan)
    spend = np.clip(18 + income / 2500 + rng.normal(0, 8, n) + (plan == "pro") * 20, 5, None)
    region = rng.choice(REGIONS, n, p=[0.3, 0.25, 0.25, 0.2])
    logit = -0.8 - 0.02 * tenure + 0.015 * (spend - 40) + 0.4 * (plan == "basic")
    churn = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    return pd.DataFrame(
        {"age": age.round(0), "income": income.round(0), "tenure_months": tenure.round(0),
         "monthly_spend": spend.round(2), "region": region, "plan": plan, LABEL: churn}
    )  # fmt: skip
