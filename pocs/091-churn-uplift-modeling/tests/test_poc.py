import numpy as np
import pandas as pd
from churn_uplift_modeling.data import FEATURES, OFFER_COST, make
from churn_uplift_modeling.evaluate import (
    balance,
    ipw_profit,
    policy_from_scores,
    qini_coefficient,
    qini_curve,
    run,
    true_profit,
)
from churn_uplift_modeling.models import SLearner, TLearner, XLearner

R = run(n=30000, seed=3)
M = R["models"]
DF = make(30000, 3)


def test_features_do_not_leak_outcomes_or_ground_truth():
    assert not {"treated", "churned", "p0", "p1", "tau", "annual_value"} & set(FEATURES)


def test_generator_is_deterministic_randomised_and_has_all_four_customer_types():
    assert make(500, 1).equals(make(500, 1))
    assert balance(DF) < 0.06 and abs(DF.treated.mean() - 0.5) < 0.02
    assert DF[["p0", "p1"]].min().min() > 0 and DF[["p0", "p1"]].max().max() < 1
    assert (DF.tau < -0.15).mean() > 0.05  # persuadables
    assert (DF.tau > 0.03).mean() > 0.01  # sleeping dogs
    assert ((DF.p0 > 0.55) & (DF.tau.abs() < 0.03)).mean() > 0.005  # lost causes
    assert ((DF.p0 < 0.08) & (DF.tau.abs() < 0.02)).mean() > 0.05  # sure things


def test_uplift_learners_rank_far_better_than_churn_risk():
    assert M["random"]["qini_per_1000"] < 3
    for n in ("S-learner", "T-learner", "X-learner"):
        assert M[n]["qini_per_1000"] > 1.3 * M["churn risk (naive)"]["qini_per_1000"], n
        assert M[n]["rank_corr_with_true_effect"] > 0.8
    assert (
        M["oracle (true effect)"]["qini_per_1000"]
        >= max(M[n]["qini_per_1000"] for n in ("S-learner", "T-learner", "X-learner")) - 0.5
    )
    assert M["churn risk (naive)"]["rank_corr_with_true_effect"] < 0.6


def test_uplift_policy_earns_much_more_than_churn_targeting_in_true_profit():
    naive, x = M["churn risk (naive)"], M["X-learner"]
    assert (
        x["gain_budget_true"] > 1.8 * naive["gain_budget_true"]
        and x["gain_value_rule_true"] > 2.5 * naive["gain_value_rule_true"]
    )
    assert (
        x["gain_value_rule_true"] > x["gain_budget_true"]
    )  # a value-aware stopping rule beats a fixed budget
    assert (
        R["baselines_true_profit_per_customer"]["treat everyone"]
        < R["baselines_true_profit_per_customer"]["treat none"] + x["gain_value_rule_true"]
    )


def test_ipw_estimates_agree_with_exact_profit_within_their_intervals():
    for n in ("churn risk (naive)", "X-learner", "oracle (true effect)"):
        est, lo, hi = M[n]["gain_value_rule_ipw"]
        assert lo <= M[n]["gain_value_rule_true"] <= hi, n


def test_ipw_profit_is_unbiased_on_a_large_sample():
    big = make(120000, 5)
    for treat in (
        np.zeros(len(big), dtype=int),
        np.ones(len(big), dtype=int),
        (big.tau < -0.1).astype(int).to_numpy(),
    ):
        assert abs(ipw_profit(big, treat) - true_profit(big, treat)) < 10


def test_x_learner_deciles_are_ordered_and_roughly_calibrated():
    d = R["x_learner_deciles"]
    assert (
        d[0]["observed"] > d[-1]["observed"] + 0.08
        and d[0]["predicted"] > d[4]["predicted"] > d[-1]["predicted"]
    )
    assert abs(d[0]["predicted"] - d[0]["true"]) < 0.06


def test_naive_targeting_spends_more_of_the_budget_on_unprofitable_offers():
    test = DF.iloc[18000:]
    from churn_uplift_modeling.models import ChurnRisk

    train = DF.iloc[:18000]
    naive = policy_from_scores(test, ChurnRisk().fit(train).score(test), budget_frac=0.2)
    x = policy_from_scores(test, XLearner().fit(train).score(test), budget_frac=0.2)
    unprofitable = (
        -test.tau * test.annual_value - OFFER_COST
    ) < 0  # true expected gain of offering is negative
    share = lambda pol: float((unprofitable & (pol == 1)).sum() / pol.sum())  # noqa: E731
    assert share(naive) > share(x) + 0.10


def test_policy_from_scores_budget_and_value_rule():
    df = pd.DataFrame({"annual_value": [1000.0, 1000.0, 100.0, 1000.0]})
    s = np.array([0.5, 0.2, 0.9, 0.01])
    assert policy_from_scores(df, s, budget_frac=0.5).tolist() == [1, 0, 1, 0]
    # expected gains: 500-60, 200-60, 90-60, 10-60 -> treat first three, never the last
    assert policy_from_scores(df, s, by_value=True).tolist() == [1, 1, 1, 0]
    assert policy_from_scores(df, s, budget_frac=0.5, by_value=True).tolist() == [1, 1, 0, 0]
    assert OFFER_COST == 60.0


def test_qini_curve_endpoints_match_the_average_effect():
    x, y = qini_curve(DF, np.random.default_rng(1).random(len(DF)))
    ate_reduction = DF[DF.treated == 0].churned.mean() - DF[DF.treated == 1].churned.mean()
    assert x[0] == 0 and x[-1] == 1 and abs(y[-1] - ate_reduction * len(DF)) < 1e-6
    assert abs(qini_coefficient(DF, np.random.default_rng(1).random(len(DF)))) < 3


def test_learners_fit_and_score_on_small_data():
    small = make(2000, 9)
    for L in (SLearner, TLearner, XLearner):
        s = L().fit(small).score(small)
        assert s.shape == (2000,) and np.isfinite(s).all()


def test_balance_detects_a_broken_randomisation():
    bad = DF.copy()
    bad["treated"] = (bad.tenure_months > bad.tenure_months.median()).astype(int)
    assert balance(bad) > 0.5
