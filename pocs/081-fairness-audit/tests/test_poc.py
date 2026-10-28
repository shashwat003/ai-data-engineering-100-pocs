import numpy as np
import pytest
from fairness_audit.audit import (
    bootstrap_ci,
    dp_stat,
    group_report,
    passes_four_fifths,
    proxy_scan,
    train_eval,
)
from fairness_audit.data import FEATURES, make_lending
from fairness_audit.main import run

R = run()


def test_generator_has_equal_ability_but_biased_labels():
    assert abs(R["true_qualified_rate"]["A"] - R["true_qualified_rate"]["B"]) < 0.05
    assert R["approval_rate_by_group_labels"]["A"] - R["approval_rate_by_group_labels"]["B"] > 0.2


def test_proxy_scan_finds_only_the_planted_proxy():
    auc = R["proxy_auc"]
    assert auc["postcode_risk"] > 0.85
    assert auc["income"] < 0.56 and auc["debt_ratio"] < 0.56


def test_baseline_fails_four_fifths_and_ci_excludes_zero():
    b = R["baseline"]
    assert not b["passes_four_fifths"] and b["disparate_impact_ratio"] < 0.8
    assert b["dp_ci95"][0] > 0.05  # a real gap, not sampling noise


def test_removing_proxy_shrinks_the_gap():
    assert (
        R["without_proxy"]["demographic_parity_diff"] < R["baseline"]["demographic_parity_diff"] / 3
    )


def test_demographic_parity_mitigation_works_and_costs_some_accuracy():
    m, b = R["mitigated_demographic_parity"], R["baseline"]
    assert m["demographic_parity_diff"] < 0.05 and m["passes_four_fifths"]
    assert m["overall_accuracy"] < b["overall_accuracy"]  # accuracy vs the biased labels drops


def test_equalized_odds_mitigation_improves_its_own_metric_not_every_metric():
    m, b = R["mitigated_equalized_odds"], R["baseline"]
    assert m["equalized_odds_diff"] < b["equalized_odds_diff"]
    # fairness criteria conflict: optimising one leaves demographic parity worse than the DP-tuned model
    assert (
        m["demographic_parity_diff"] > R["mitigated_demographic_parity"]["demographic_parity_diff"]
    )


def test_group_report_on_a_known_toy_case():
    y = np.array([1, 1, 0, 0, 1, 1, 0, 0])
    p = np.array([1, 1, 0, 0, 1, 0, 0, 0])
    g = np.array(["A"] * 4 + ["B"] * 4)
    r = group_report(y, p, g)
    assert (
        r["by_group"]["A"]["selection_rate"] == 0.5 and r["by_group"]["B"]["selection_rate"] == 0.25
    )
    assert r["demographic_parity_diff"] == 0.25 and r["disparate_impact_ratio"] == 0.5
    assert not passes_four_fifths(r)


def test_perfectly_fair_predictions_pass():
    y = np.array([1, 0, 1, 0])
    g = np.array(["A", "A", "B", "B"])
    r = group_report(y, y, g)
    assert (
        r["demographic_parity_diff"] == 0
        and r["equalized_odds_diff"] == 0
        and passes_four_fifths(r)
    )


def test_no_planted_bias_means_no_flag():
    df = make_lending(proxy_strength=0.0, label_bias=0.0, seed=3)
    m, _, te, cols = train_eval(df, FEATURES)
    r = group_report(te.approved, m.predict(te[cols]), te.group)
    assert r["disparate_impact_ratio"] > 0.8 and passes_four_fifths(r)


def test_bootstrap_is_deterministic_and_handles_single_group_resamples():
    y = np.array([1, 0, 1, 0, 1, 0])
    p = np.array([1, 0, 0, 0, 1, 1])
    g = np.array(["A", "A", "A", "B", "B", "B"])
    assert bootstrap_ci(y, p, g, dp_stat, n_boot=50) == bootstrap_ci(y, p, g, dp_stat, n_boot=50)


def test_proxy_scan_needs_two_groups_to_mean_anything():
    df = make_lending(n=400)
    assert set(proxy_scan(df[FEATURES], df.group)) == set(FEATURES)


def test_run_is_reproducible():
    assert run() == R


def test_small_group_is_flagged_by_wide_interval():
    df = make_lending(n=300, seed=1)
    m, _, te, cols = train_eval(df, FEATURES)
    lo, hi = bootstrap_ci(te.approved, m.predict(te[cols]), te.group, dp_stat)
    assert hi - lo > 0.1  # at n~90 test rows a gap estimate is very uncertain


@pytest.mark.parametrize("bad", [1, 2])
def test_mismatched_lengths_raise(bad):
    with pytest.raises(ValueError):
        group_report(np.array([1, 0, 1]), np.array([1, 0, 1][:-bad]), np.array(["A", "B", "A"]))
