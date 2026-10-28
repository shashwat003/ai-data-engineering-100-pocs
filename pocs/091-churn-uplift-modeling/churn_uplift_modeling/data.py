"""Synthetic subscription customers from a randomised retention-offer experiment with KNOWN individual effects."""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURES = [
    "tenure_months",
    "monthly_spend",
    "support_tickets",
    "logins_30d",
    "discount_sensitivity",
    "monthly_contract",
]
OFFER_COST = 60.0  # two months at half price


def sigmoid(z):
    return 1 / (1 + np.exp(-z))


def make(n: int = 50000, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    tenure = np.clip(rng.gamma(2.5, 10, n), 1, 120)
    spend = np.clip(rng.normal(60, 20, n), 15, 200)
    tickets = rng.poisson(1.2, n)
    logins = np.clip(rng.poisson(14, n) + rng.normal(0, 3, n), 0, 60)
    sens = rng.normal(0, 1, n)
    monthly = (rng.random(n) < 0.55).astype(int)
    z0 = -1.5 - 0.03 * tenure + 0.45 * tickets - 0.04 * logins + 1.1 * monthly + 0.45 * sens
    p0 = sigmoid(z0)  # churn probability without the offer
    # The offer helps price-sensitive customers who are at risk, does nothing for those who will stay anyway,
    # and slightly backfires for loyal, happy customers (a "sleeping dog": it prompts them to review their plan).
    # persuadables: price-sensitive customers at MEDIUM risk. Low-risk customers (sure things) and very high-risk ones
    # (lost causes) barely react, so ranking by churn risk puts many lost causes first.
    bump = sigmoid((p0 - 0.12) / 0.03) * sigmoid((0.55 - p0) / 0.05)
    help_ = -0.40 * sigmoid(2.5 * sens) * bump
    backfire = 0.06 * ((tenure > 40) & (tickets <= 1) & (monthly == 0))
    tau = (
        help_ + backfire
    )  # change in churn probability caused by the offer (negative = fewer churners)
    p1 = np.clip(p0 + tau, 0.005, 0.995)
    w = (rng.random(n) < 0.5).astype(int)  # randomised assignment
    churn = (rng.random(n) < np.where(w == 1, p1, p0)).astype(int)
    return pd.DataFrame({"tenure_months": tenure, "monthly_spend": spend, "support_tickets": tickets, "logins_30d": logins, "discount_sensitivity": sens, "monthly_contract": monthly,
                         "treated": w, "churned": churn, "p0": p0, "p1": p1, "tau": p1 - p0, "annual_value": spend * 12})  # fmt: skip
