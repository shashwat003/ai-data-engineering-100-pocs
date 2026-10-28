import numpy as np
import pytest
from llm_as_judge_calibration.judge import (
    SimJudge,
    accuracy,
    bootstrap_ci,
    features,
    fit_logistic,
    kappa,
    kendall_tau,
    make_pairs,
    position_bias_estimate,
    predict,
    prefers_a,
)

TEST = make_pairs(3000, seed=1)


def test_pairs_are_deterministic_and_human_follows_quality():
    a, b = make_pairs(500, 3), make_pairs(500, 3)
    assert np.array_equal(a.human, b.human) and (a.la > 0).all()
    assert ((a.qa > a.qb).astype(int) == a.human).mean() > 0.85


def test_metric_known_values():
    y = np.array([1, 1, 0, 0])
    assert accuracy(np.array([1, 0, 0, 0]), y) == 0.75
    assert accuracy(
        np.array([1, 0, 0, 0]), y, np.array([True, False, True, True])
    ) == pytest.approx(1.0)
    assert (
        kappa(y, y) == 1.0 and kappa(np.array([1, 1, 1, 1]), y) == 0.0 and kappa(1 - y, y) == -1.0
    )
    assert kendall_tau([1, 2, 3], [1, 2, 3]) == 1.0 and kendall_tau([1, 2, 3], [3, 2, 1]) == -1.0


def test_unbiased_judge_has_no_position_bias_and_good_agreement():
    j = SimJudge()
    assert abs(position_bias_estimate(j, TEST)) < 0.02
    assert accuracy(prefers_a(j, TEST)[0], TEST.human) > 0.88


def test_position_bias_is_detected_with_the_right_sign_and_hurts_accuracy():
    biased = SimJudge(position_bias=1.0)
    assert position_bias_estimate(biased, TEST) > 0.08
    assert position_bias_estimate(SimJudge(position_bias=-1.0), TEST) < -0.08
    assert accuracy(prefers_a(biased, TEST)[0], TEST.human) < accuracy(
        prefers_a(SimJudge(), TEST)[0], TEST.human
    )


def test_swapping_positions_cancels_position_bias():
    j = SimJudge(position_bias=1.2)
    single = accuracy(prefers_a(j, TEST, "single")[0], TEST.human)
    swapped = accuracy(prefers_a(j, TEST, "swap_avg")[0], TEST.human)
    assert swapped > single + 0.03
    assert (
        abs(position_bias_estimate(j, TEST) - 0.12) < 0.1
    )  # bias remains visible in raw judgements


def test_abstaining_on_order_disagreement_trades_coverage_for_accuracy():
    j = SimJudge(position_bias=1.0)
    pred, mask = prefers_a(j, TEST, "swap_agree")
    assert 0.5 < mask.mean() < 0.95
    assert accuracy(pred, TEST.human, mask) > accuracy(prefers_a(j, TEST)[0], TEST.human) + 0.05


def test_verbosity_bias_is_invisible_to_a_swap_test_but_calibration_corrects_it():
    j = SimJudge(verbosity_bias=3.0, position_bias=0.0)
    assert abs(position_bias_estimate(j, TEST)) < 0.03  # swapping does not reveal it
    raw = accuracy(prefers_a(j, TEST)[0], TEST.human)
    calib = make_pairs(400, seed=5)
    w = fit_logistic(features(j, calib), calib.human)
    assert w[1] < -0.2  # the fitted model learns to subtract the length advantage
    assert accuracy(predict(w, features(j, TEST)), TEST.human) > raw + 0.01


def test_calibration_improves_with_more_labels_on_average():
    j = SimJudge(position_bias=1.0, verbosity_bias=1.5)

    def mean_acc(n):
        return np.mean(
            [
                accuracy(
                    predict(
                        fit_logistic(features(j, c := make_pairs(n, seed=50 + s)), c.human),
                        features(j, TEST),
                    ),
                    TEST.human,
                )
                for s in range(8)
            ]
        )

    assert mean_acc(200) > mean_acc(10)


def test_bootstrap_interval_contains_the_mean_and_is_ordered():
    v = np.random.default_rng(0).normal(0.9, 0.02, 40)
    lo, hi = bootstrap_ci(v)
    assert lo < v.mean() < hi and hi - lo < 0.02
    assert bootstrap_ci(v) == bootstrap_ci(v)
