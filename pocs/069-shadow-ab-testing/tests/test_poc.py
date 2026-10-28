import math

import numpy as np
import pytest
from fastapi.testclient import TestClient
from shadow_ab_testing.app import make_app
from shadow_ab_testing.main import cuped_gain
from shadow_ab_testing.router import ShadowRouter, assign, bucket
from shadow_ab_testing.stats import (
    cuped,
    msprt_lambda,
    sample_size_per_arm,
    srm_pvalue,
    two_prop_p,
    welch_p,
)


def test_bucketing_is_deterministic_uniform_and_salt_dependent():
    assert (
        bucket("u1", "e") == bucket("u1", "e")
        and bucket("u1", "e") != bucket("u1", "e2")
        and bucket("u1", "e", "a") != bucket("u1", "e", "b")
    )
    vals = np.array([bucket(f"u{i}", "e") for i in range(20000)])
    assert 0.0 <= vals.min() and vals.max() < 1 and abs(vals.mean() - 0.5) < 0.01
    share = np.mean([assign(f"u{i}", "e", 0.2) == "treatment" for i in range(20000)])
    assert abs(share - 0.2) < 0.01


def test_srm_detects_a_broken_split_but_not_a_good_one():
    arms = [assign(f"u{i}", "x") for i in range(20000)]
    nt = arms.count("treatment")
    assert srm_pvalue(20000 - nt, nt) > 0.001
    assert srm_pvalue(9000, 11000) < 1e-10
    assert srm_pvalue(5000, 5000) == pytest.approx(1.0)


def test_fixed_horizon_tests_agree_with_known_cases():
    assert two_prop_p(100, 1000, 100, 1000) == pytest.approx(1.0)
    assert two_prop_p(100, 1000, 160, 1000) < 1e-4
    rng = np.random.default_rng(0)
    assert welch_p(rng.normal(0, 1, 500), rng.normal(0.5, 1, 500)) < 1e-4


def test_sample_size_matches_textbook_value():
    assert (
        14_000 < sample_size_per_arm(0.10, 0.01) < 15_500
    )  # ~14.7k per arm for 10% -> 11% at 80% power
    assert sample_size_per_arm(0.10, 0.02) < sample_size_per_arm(0.10, 0.01) / 3


def test_msprt_is_one_at_zero_effect_and_grows_with_evidence():
    assert msprt_lambda(0.0, 1e-3, 0.01) == pytest.approx(math.sqrt(1e-3 / (1e-3 + 0.01)))
    assert (
        msprt_lambda(0.2, 1e-3, 0.01)
        > msprt_lambda(0.1, 1e-3, 0.01)
        > msprt_lambda(0.0, 1e-3, 0.01)
    )


def test_peeking_inflates_false_positives_but_msprt_does_not():
    from shadow_ab_testing.main import monte_carlo

    r = monte_carlo(0.0, sims=120, seed=5)
    assert r["peek_naive"] > 0.15  # nominal 5% inflated by repeated looks
    assert r["msprt"] <= 0.06  # anytime-valid control
    r2 = monte_carlo(0.15, sims=60, seed=6)
    assert r2["msprt"] > 0.9  # still finds a real effect


def test_cuped_reduces_variance_without_biasing_the_mean():
    rng = np.random.default_rng(2)
    pre = rng.normal(10, 3, 4000)
    y = 0.8 * pre + rng.normal(0, 1.5, 4000)
    adj = cuped(y, pre)
    assert adj.var() < 0.4 * y.var() and abs(adj.mean() - y.mean()) < 1e-9
    assert cuped_gain()["reduction"] > 0.6
    assert (
        abs(cuped(rng.normal(size=4000), rng.normal(size=4000)).var() - 1) < 0.1
    )  # useless covariate: no change


def test_shadow_never_changes_what_the_user_gets_even_when_the_candidate_crashes():
    def boom(f):
        raise ValueError("candidate bug")

    r = ShadowRouter(lambda f: f["x"] * 2, boom)
    assert [r.predict(f"u{i}", {"x": i}) for i in range(50)] == [i * 2 for i in range(50)]
    assert (
        all(row["error"] == "ValueError" and row["shadow"] is None for row in r.log.rows)
        and len(r.log.rows) == 50
    )


def test_shadow_sampling_share_and_agreement_logging():
    r = ShadowRouter(lambda f: 1.0, lambda f: 1.1, shadow_share=0.25)
    for i in range(4000):
        r.predict(f"u{i}", {})
    assert abs(len(r.log.rows) / 4000 - 0.25) < 0.03
    assert all(abs(x["shadow"] - x["prod"] - 0.1) < 1e-9 for x in r.log.rows)


def test_fastapi_endpoints():
    r = ShadowRouter(lambda f: f["x"], lambda f: f["x"] + 1)
    c = TestClient(make_app(r))
    resp = c.post("/predict", json={"user_id": "u7", "features": {"x": 3.0}})
    assert (
        resp.status_code == 200
        and resp.json()["score"] == 3.0
        and resp.json()["arm"] in ("control", "treatment")
    )
    assert c.get("/shadow/summary").json() == {
        "requests_shadowed": 1,
        "shadow_errors": 0,
        "mean_abs_diff": 1.0,
    }
    assert c.post("/predict", json={"features": {}}).status_code == 422
