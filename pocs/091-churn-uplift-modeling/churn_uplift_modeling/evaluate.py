"""Uplift evaluation on a randomised test set: Qini curve, decile calibration, and profit of targeting policies."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data import FEATURES, OFFER_COST, make
from .models import ChurnRisk, SLearner, TLearner, XLearner


def qini_curve(
    df: pd.DataFrame, score: np.ndarray, points: int = 20
) -> tuple[np.ndarray, np.ndarray]:
    """Cumulative incremental retained customers when treating the top-k by score (randomised data)."""
    d = df.assign(s=score).sort_values("s", ascending=False).reset_index(drop=True)
    retained = 1 - d["churned"].to_numpy()
    t = d["treated"].to_numpy()
    xs, ys = [0.0], [0.0]
    for f in np.linspace(1 / points, 1, points):
        k = int(round(f * len(d)))
        rt, rc = retained[:k][t[:k] == 1].sum(), retained[:k][t[:k] == 0].sum()
        nt, nc = max(1, (t[:k] == 1).sum()), max(1, (t[:k] == 0).sum())
        xs.append(f)
        ys.append((rt / nt - rc / nc) * k)
    return np.array(xs), np.array(ys)


def qini_coefficient(df: pd.DataFrame, score: np.ndarray) -> float:
    """Area between the Qini curve and the random-targeting diagonal, divided by n."""
    x, y = qini_curve(df, score)
    random_line = x * y[-1]
    return float(
        np.trapezoid(y - random_line, x) / len(df) * 1000
    )  # per 1000 customers, for readability


def ipw_profit(df: pd.DataFrame, treat: np.ndarray, p: float = 0.5) -> float:
    """Unbiased estimate (randomised test) of the profit per customer if exactly `treat` were offered the discount.

    Profit per customer = annual value if retained - offer cost if treated. Under the policy a customer's outcome is the
    treated outcome if treat else the control outcome, and the randomised arm that matches the policy provides it.
    """
    retained = 1 - df["churned"].to_numpy()
    v = df["annual_value"].to_numpy()
    t = df["treated"].to_numpy()
    follows = np.where(treat == 1, t == 1, t == 0)
    w = np.where(treat == 1, 1 / p, 1 / (1 - p)) * follows
    return float(np.mean(w * (retained * v - treat * OFFER_COST)))


def true_profit(df: pd.DataFrame, treat: np.ndarray) -> float:
    """Exact expected profit using the generator's churn probabilities (only possible on synthetic data)."""
    p = np.where(treat == 1, df["p1"], df["p0"])
    return float(np.mean((1 - p) * df["annual_value"] - treat * OFFER_COST))


def policy_from_scores(
    df: pd.DataFrame, score: np.ndarray, budget_frac: float | None = None, by_value: bool = False
) -> np.ndarray:
    """Treat when expected gain > cost; optionally capped to a budget fraction of customers (highest expected gain first)."""
    gain = score * df["annual_value"].to_numpy() - OFFER_COST if by_value else score
    treat = (gain > 0).astype(int) if by_value else np.zeros(len(df), dtype=int)
    if budget_frac is not None:
        k = int(budget_frac * len(df))
        order = np.argsort(-gain)[:k]
        out = np.zeros(len(df), dtype=int)
        out[order] = 1
        return out * (gain > 0 if by_value else 1)
    return treat


def balance(df: pd.DataFrame) -> float:
    """Largest standardised mean difference of features between arms (randomisation check; < 0.1 is fine)."""
    t, c = df[df.treated == 1], df[df.treated == 0]
    return float(
        max(
            abs(t[f].mean() - c[f].mean()) / np.sqrt((t[f].var() + c[f].var()) / 2)
            for f in FEATURES
        )
    )


def boot_gain(
    df: pd.DataFrame, treat: np.ndarray, n_boot: int = 200, seed: int = 0
) -> tuple[float, float, float]:
    """IPW profit gain vs treating nobody, with a 95% bootstrap interval (per customer)."""
    rng = np.random.default_rng(seed)
    none = np.zeros(len(df), dtype=int)
    vals = []
    for _ in range(n_boot):
        i = rng.integers(0, len(df), len(df))
        s = df.iloc[i]
        vals.append(ipw_profit(s, treat[i]) - ipw_profit(s, none[i]))
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return float(ipw_profit(df, treat) - ipw_profit(df, none)), float(lo), float(hi)


def run(n: int = 50000, seed: int = 11, budget: float = 0.20) -> dict:
    df = make(n, seed)
    train, test = (
        df.iloc[: n * 6 // 10].reset_index(drop=True),
        df.iloc[n * 6 // 10 :].reset_index(drop=True),
    )
    models = {
        "churn risk (naive)": ChurnRisk().fit(train),
        "S-learner": SLearner().fit(train),
        "T-learner": TLearner().fit(train),
        "X-learner": XLearner().fit(train),
    }
    scores = {k: m.score(test) for k, m in models.items()}
    scores["oracle (true effect)"] = -test["tau"].to_numpy()
    scores["random"] = np.random.default_rng(0).random(len(test))
    out: dict = {
        "n_train": len(train),
        "n_test": len(test),
        "balance_smd": round(balance(test), 3),
        "models": {},
    }
    base = {
        "treat none": ipw_profit(test, np.zeros(len(test), dtype=int)),
        "treat everyone": ipw_profit(test, np.ones(len(test), dtype=int)),
    }
    base_true = true_profit(test, np.zeros(len(test), dtype=int))
    out["baselines_ipw_profit_per_customer"] = {k: round(v, 2) for k, v in base.items()}
    out["baselines_true_profit_per_customer"] = {
        "treat none": round(true_profit(test, np.zeros(len(test), dtype=int)), 2),
        "treat everyone": round(true_profit(test, np.ones(len(test), dtype=int)), 2),
    }
    for name, s in scores.items():
        is_uplift = name != "churn risk (naive)" and name != "random"
        pol_budget = policy_from_scores(test, s, budget_frac=budget)
        pol_value = policy_from_scores(test, s, by_value=True) if is_uplift else pol_budget
        out["models"][name] = {
            "qini_per_1000": round(qini_coefficient(test, s), 2),
            "tau_rmse": round(float(np.sqrt(np.mean((s - (-test["tau"].to_numpy())) ** 2))), 3) if name in ("S-learner", "T-learner", "X-learner") else None,
            "rank_corr_with_true_effect": round(float(pd.Series(s).corr(pd.Series(-test["tau"].to_numpy()), method="spearman")), 3),
            "gain_budget_ipw": [round(x, 2) for x in boot_gain(test, pol_budget)], "gain_budget_true": round(true_profit(test, pol_budget) - base_true, 2),
            "gain_value_rule_ipw": [round(x, 2) for x in boot_gain(test, pol_value)], "gain_value_rule_true": round(true_profit(test, pol_value) - base_true, 2),
            "treated_share_value_rule": round(float(pol_value.mean()), 3),
            "sleeping_dogs_treated_budget": round(float(((test["tau"] > 0) & (pol_budget == 1)).sum() / max(1, pol_budget.sum())), 3),
        }  # fmt: skip
    # decile calibration of the best learner: predicted vs observed uplift per decile (randomised data)
    s = scores["X-learner"]
    d = test.assign(
        s=s, dec=pd.qcut(pd.Series(s).rank(method="first", ascending=False), 10, labels=False)
    )
    rows = []
    for k in range(10):
        g = d[d.dec == k]
        obs = g[g.treated == 0]["churned"].mean() - g[g.treated == 1]["churned"].mean()
        rows.append(
            {
                "decile": k + 1,
                "predicted": round(float(g.s.mean()), 3),
                "observed": round(float(obs), 3),
                "true": round(float(-g.tau.mean()), 3),
            }
        )
    out["x_learner_deciles"] = rows
    out["budget_fraction"] = budget
    return out
