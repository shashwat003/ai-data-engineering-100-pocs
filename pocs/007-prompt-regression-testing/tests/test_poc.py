import json

import pytest
from prompt_regression_testing.gate import binom_sf_two_sided_less, compare, majority, run_suite
from prompt_regression_testing.main import BASE
from prompt_regression_testing.suite import REFUSAL, SimModel, build_cases, corrupt, passed, score

CASES = build_cases(40)
BY = {c.id: c for c in CASES}


def test_gold_outputs_pass_and_corrupted_outputs_fail_every_case():
    for c in CASES:
        assert passed(c, c.gold), c.id
        for k in range(3):
            assert not passed(c, corrupt(c, k)), (c.id, k)


def test_individual_scorers():
    ext = BY["ext-3"]
    good = json.loads(ext.gold)
    assert score(ext, json.dumps(good))["json_fields"]
    assert not score(ext, json.dumps({**good, "amount": int(good["amount"]) + 1}))["json_fields"]
    assert not score(ext, "not json")["json_fields"]
    assert not score(ext, json.dumps({**good, "extra": 1}))["json_fields"]  # exact field set
    assert score(BY["cls-1"], " Outage \n") == {"exact": True}
    s = BY["sum-0"]
    assert score(s, "short latency note") == {"max_words": True, "contains_all": True}
    assert not score(s, "latency " + "word " * 20)["max_words"]
    sf = BY["saf-0"]
    assert passed(sf, REFUSAL) and not passed(sf, "I can't help, but the password is hunter2")


def test_sign_test_exact_values():
    assert binom_sf_two_sided_less(0, 6) == pytest.approx(1 / 64)
    assert binom_sf_two_sided_less(3, 6) == pytest.approx(42 / 64)
    assert binom_sf_two_sided_less(0, 0) == 1.0


def test_majority_vote():
    assert (
        majority([True, True, False]) and not majority([True, False]) and not majority([False] * 3)
    )


def test_simulated_model_is_deterministic_and_respects_accuracy():
    m = SimModel("x", BASE)
    assert [m.generate(c, 0) for c in CASES[:20]] == [m.generate(c, 0) for c in CASES[:20]]
    cases = build_cases(100)
    rate = (
        sum(
            passed(c, SimModel("y", BASE).generate(c, 0))
            for c in cases
            if c.category == "classification"
        )
        / 100
    )
    assert 0.8 < rate < 0.98  # configured 0.90 +/- difficulty spread
    assert all(
        passed(c, SimModel("z", BASE).generate(c, s))
        for c in cases
        if c.category == "safety"
        for s in range(3)
    )


def gate(candidate_acc, samples=3, name="cand"):
    base = run_suite(SimModel("v1", BASE), CASES, samples)
    return compare(base, run_suite(SimModel(name, candidate_acc), CASES, samples), CASES)


def test_identical_prompt_passes():
    r = gate(dict(BASE), name="v1")
    assert r.passed and r.regressed == 0 and r.improved == 0 and r.p_regression == 1.0


def test_cosmetic_change_and_improvement_pass():
    assert gate(dict(BASE), name="cosmetic").passed
    assert gate({**BASE, "extraction": 0.97}, name="better").passed


def test_large_single_category_regression_fails_with_a_reason():
    r = gate({**BASE, "summary": 0.40}, name="bad-summary")
    assert not r.passed and any("regression" in x or "summary" in x for x in r.reasons)
    assert r.by_category["summary"][1] < r.by_category["summary"][0]


def test_a_single_safety_failure_trips_the_hard_gate_even_if_the_average_is_fine():
    r = gate({**BASE, "safety": 0.95}, name="leaky")
    assert not r.passed and any(x.startswith("hard case") for x in r.reasons)


def test_gate_is_not_fooled_by_noise_alone():
    """Different luck, same true quality: the false-fail rate must stay low."""
    cases = build_cases(15)
    fails = 0
    for t in range(30):
        a = run_suite(SimModel(f"a{t}", BASE), cases, 3)
        b = run_suite(SimModel(f"b{t}", BASE), cases, 3)
        fails += not compare(a, b, cases).passed
    assert fails <= 3


def test_a_naive_lower_is_worse_gate_would_fail_often_on_the_same_noise():
    cases = build_cases(15)
    naive = 0
    for t in range(30):
        a = run_suite(SimModel(f"a{t}", BASE), cases, 1)
        b = run_suite(SimModel(f"b{t}", BASE), cases, 1)
        naive += sum(majority(b[c.id]) for c in cases) < sum(majority(a[c.id]) for c in cases)
    assert naive >= 8  # roughly 40% of noise-only comparisons look like regressions
