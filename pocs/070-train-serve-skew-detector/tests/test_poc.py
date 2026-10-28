import numpy as np
import pytest
from train_serve_skew_detector.detect import compare, leakage_check, stale_check
from train_serve_skew_detector.features import BUGS, DAY, compute, make_events
from train_serve_skew_detector.main import diagnose, queries

EV = make_events()
Q = queries(EV, 800)


def test_clean_offline_and_online_paths_agree_exactly():
    off, on = compute(EV, Q, "offline"), compute(EV, Q, "online")
    for f in off:
        assert off[f] == on[f] or np.allclose(
            np.array(off[f], dtype=float), np.array(on[f], dtype=float), equal_nan=True
        ), f
    rep = compare(off, on, Q, EV)
    assert all(r["mismatch_rate"] == 0 and r["cause"] is None for r in rep.values())


def test_features_are_point_in_time_correct():
    u, t = Q[0]
    f = compute(EV, [(u, t)], "offline")
    m = (EV["user"] == u) & (EV["ts"] <= t) & (EV["ts"] > t - 7 * DAY)
    assert f["n_orders_7d"][0] == int(m.sum())
    f2 = compute(EV, [(u, t)], "offline", frozenset({"future_leak"}))
    assert f2["n_orders_7d"][0] >= f["n_orders_7d"][0]


@pytest.mark.parametrize(
    "bug,prefix,feature",
    [
        ("unit_scaling", "unit_scaling", "avg_order_value_30d"),
        ("window_off_by_one", "window_off_by_one", "n_orders_7d"),
        ("null_default", "null_default", "last_login_hours"),
        ("category_case", "category_case", "country"),
    ],
)
def test_serving_bug_is_diagnosed_with_the_right_cause_on_the_right_feature(bug, prefix, feature):
    _, found = diagnose(EV, Q, {bug})
    assert found.get(feature, "").startswith(prefix), found
    assert not any(
        v for k, v in found.items() if k not in (feature, "__training_data__")
    )  # no collateral findings


def test_unit_scaling_reports_the_factor():
    _, found = diagnose(EV, Q, {"unit_scaling"})
    assert found["avg_order_value_30d"] == "unit_scaling(x100)"


def test_stale_cache_detected_by_matching_the_previous_days_value():
    off, on = compute(EV, Q, "offline"), compute(EV, Q, "online", frozenset({"stale_cache"}))
    assert stale_check(off, on, EV, Q) > 0.9
    assert stale_check(off, off, EV, Q) == 0.0


def test_future_leak_found_only_by_point_in_time_recompute_not_by_comparing_paths():
    off_leaky = compute(EV, Q, "offline", frozenset({"future_leak"}))
    on_clean = compute(EV, Q, "online")
    rep = compare(off_leaky, on_clean, Q, EV)
    assert rep["n_orders_7d"]["mismatch_rate"] > 0.1  # paths disagree...
    assert (
        leakage_check(EV, Q, off_leaky)["n_orders_7d"] > 0.1
    )  # ...and the as-of recompute pins the blame on training
    assert max(leakage_check(EV, Q, compute(EV, Q, "offline")).values()) == 0.0


def test_clean_pipeline_raises_no_findings():
    _, found = diagnose(EV, Q, set())
    assert found == {}


def test_several_simultaneous_bugs_are_all_reported():
    _, found = diagnose(EV, Q, {"unit_scaling", "null_default", "category_case"})
    assert {v.split("(")[0] for v in found.values()} >= {
        "unit_scaling",
        "null_default",
        "category_case",
    }


def test_float_noise_below_tolerance_is_not_skew():
    off = compute(EV, Q, "offline")
    on = {
        k: [v * (1 + 1e-9) if isinstance(v, float) and not np.isnan(v) else v for v in vals]
        for k, vals in off.items()
    }
    assert all(r["cause"] is None for r in compare(off, on, Q, EV).values())


def test_all_known_bugs_are_covered_by_the_generator():
    assert set(BUGS) == {
        "unit_scaling",
        "window_off_by_one",
        "null_default",
        "category_case",
        "stale_cache",
        "future_leak",
    }
