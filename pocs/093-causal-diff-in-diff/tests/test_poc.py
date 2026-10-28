import numpy as np
import pytest
import statsmodels.api as sm
from causal_diff_in_diff.did import (
    Panel,
    bootstrap_se,
    classic_2x2,
    group_time_att,
    overall_att,
    pretrend_test,
    simulate,
    twfe,
)


def test_simulation_shapes_truth_and_cohort_shares():
    p = simulate(n=400, seed=1)
    assert p.y.shape == (400, 20) and set(np.unique(p.cohort)) == {-1, 8, 14}
    assert 0.25 < (p.cohort < 0).mean() < 0.42
    assert p.true_att > 0 and (p.effect[p.cohort < 0] == 0).all() and (p.effect[:, :8] == 0).all()
    assert simulate(seed=3).y.tolist() == simulate(seed=3).y.tolist()


def test_within_transformation_twfe_equals_the_dummy_variable_regression():
    p = simulate(n=60, t=10, cohorts=(4, 7), effect={4: 2.0, 7: 1.0}, seed=4)
    n, t = p.y.shape
    unit, time = np.repeat(np.arange(n), t), np.tile(np.arange(t), n)
    X = np.column_stack(
        [
            p.treated().reshape(-1),
            (unit[:, None] == np.arange(1, n)[None, :]).astype(float),
            (time[:, None] == np.arange(1, t)[None, :]).astype(float),
            np.ones(n * t),
        ]
    )
    ref = sm.OLS(p.y.reshape(-1), X).fit(cov_type="cluster", cov_kwds={"groups": unit})
    est, se = twfe(p)
    assert est == pytest.approx(ref.params[0], abs=1e-9)
    assert se == pytest.approx(
        ref.bse[0], rel=0.03
    )  # same estimator up to the small-sample correction


def test_noiseless_group_time_estimator_recovers_the_truth():
    p = simulate(noise=0.0, seed=2)
    # unit/time effects cancel in the double difference, so ATT(g,t) equals the true effect at (g,t) in expectation;
    # with random unit effects the cohort means differ slightly, so allow a small sampling tolerance
    assert overall_att(p) == pytest.approx(p.true_att, abs=0.35)


def test_twfe_is_unbiased_for_a_static_effect_but_biased_when_effects_grow():
    static = [
        twfe(simulate(seed=10 + s, dynamic=0.0, effect={8: 2.0, 14: 2.0}))[0] - 2.0
        for s in range(30)
    ]
    growing_t = [
        twfe(simulate(seed=10 + s, dynamic=0.8))[0] - simulate(seed=10 + s, dynamic=0.8).true_att
        for s in range(30)
    ]
    growing_g = [
        overall_att(simulate(seed=10 + s, dynamic=0.8))
        - simulate(seed=10 + s, dynamic=0.8).true_att
        for s in range(30)
    ]
    assert abs(np.mean(static)) < 0.1
    assert np.mean(growing_t) < -0.5  # forbidden comparisons: large downward bias
    assert abs(np.mean(growing_g)) < 0.15


def test_group_time_att_skips_the_reference_period_and_covers_all_other_cells():
    p = simulate(seed=5)
    att = group_time_att(p)
    assert (8, 7) not in att and (14, 13) not in att and (8, 8) in att and len(att) == 2 * 19
    pre = [v for (g, t), v in att.items() if t < g]
    assert abs(np.mean(pre)) < 0.3  # no effect before adoption


def test_classic_2x2_ci_covers_the_truth_about_95_percent_of_the_time():
    hit = 0
    for s in range(120):
        p = simulate(cohorts=(8,), effect={8: 2.0}, dynamic=0.6, seed=300 + s, share_never=0.5)
        est, se = classic_2x2(p, 8)
        hit += abs(est - (2.0 + 0.6 * 3)) <= 1.96 * se
    assert 0.90 <= hit / 120 <= 0.995


def test_pretrend_test_has_correct_size_and_good_power():
    ok = np.mean(
        [
            pretrend_test(
                simulate(cohorts=(12,), effect={12: 2.0}, seed=800 + s, share_never=0.5), 12
            )
            < 0.05
            for s in range(100)
        ]
    )
    bad = np.mean(
        [
            pretrend_test(
                simulate(
                    cohorts=(12,), effect={12: 2.0}, trend_gap=0.15, seed=800 + s, share_never=0.5
                ),
                12,
            )
            < 0.05
            for s in range(100)
        ]
    )
    assert ok < 0.12 and bad > 0.9


def test_ignoring_a_pretrend_violation_biases_the_estimate():
    est = np.mean(
        [
            classic_2x2(
                simulate(
                    cohorts=(12,),
                    effect={12: 2.0},
                    dynamic=0.0,
                    trend_gap=0.15,
                    seed=700 + s,
                    share_never=0.5,
                ),
                12,
            )[0]
            for s in range(40)
        ]
    )
    assert est > 2.0 + 0.4  # four periods x 0.15 drift


def test_bootstrap_se_is_close_to_the_sampling_sd():
    ests = [overall_att(simulate(seed=2000 + s, n=200)) for s in range(40)]
    se = bootstrap_se(simulate(seed=2000, n=200), overall_att, reps=60)
    assert 0.5 < se / np.std(ests, ddof=1) < 1.8


def test_panel_helpers():
    p = Panel(np.zeros((2, 4)), np.array([2, -1]), 0.0, np.zeros((2, 4)))
    assert p.treated().tolist() == [[0, 0, 1, 1], [0, 0, 0, 0]]
