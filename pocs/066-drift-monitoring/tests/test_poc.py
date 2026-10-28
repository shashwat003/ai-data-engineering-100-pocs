import math

import numpy as np
import pytest
from drift_monitoring.detectors import (
    chi2_homogeneity,
    chi2_sf,
    js_divergence,
    ks_2samp,
    proportion_z,
    psi_categorical,
    psi_numeric,
)
from drift_monitoring.monitor import Monitor, Policy
from drift_monitoring.simulate import N_REF, N_WIN, make_data, run_stream, study


def test_chi2_survival_matches_reference_values():
    assert chi2_sf(3.841, 1) == pytest.approx(0.05, abs=1e-3)
    assert chi2_sf(5.991, 2) == pytest.approx(0.05, abs=1e-3)
    assert chi2_sf(0.0, 3) == 1.0
    assert chi2_sf(18.307, 10) == pytest.approx(0.05, abs=1e-3)
    assert chi2_sf(100, 3) < 1e-15


def test_ks_known_cases():
    rng = np.random.default_rng(0)
    a = rng.normal(size=2000)
    d0, p0 = ks_2samp(a, a.copy())
    assert d0 == 0 and p0 == 1
    d1, p1 = ks_2samp(a, a + 1.0)
    assert d1 > 0.3 and p1 < 1e-10
    assert ks_2samp(np.array([1.0, 2, 3]), np.array([4.0, 5, 6]))[0] == 1.0  # disjoint supports


def test_ks_p_values_are_roughly_uniform_under_the_null():
    rng = np.random.default_rng(1)
    ps = np.array([ks_2samp(rng.normal(size=500), rng.normal(size=500))[1] for _ in range(300)])
    assert 0.03 < (ps < 0.05).mean() < 0.08 and 0.35 < np.median(ps) < 0.65


def test_psi_properties():
    rng = np.random.default_rng(2)
    a = rng.normal(size=20000)
    assert psi_numeric(a, rng.normal(size=20000)) < 0.02
    assert psi_numeric(a, a + 1.0) > 0.25  # shift of one std is "significant" (> 0.2)
    assert psi_numeric(a, a + 0.1) < psi_numeric(a, a + 0.5)  # monotone in shift
    x = np.array(["a"] * 50 + ["b"] * 50)
    assert (
        psi_categorical(x, x) == 0 and psi_categorical(x, np.array(["a"] * 90 + ["b"] * 10)) > 0.5
    )
    assert psi_categorical(x, np.array(["c"] * 100)) > 5  # unseen category does not crash


def test_chi2_homogeneity_and_proportions_and_js():
    same = np.array(["a", "b"] * 500)
    assert chi2_homogeneity(same, same.copy())[1] == 1.0
    assert (
        chi2_homogeneity(np.array(["a"] * 900 + ["b"] * 100), np.array(["a"] * 500 + ["b"] * 500))[
            1
        ]
        < 1e-10
    )
    z, p = proportion_z(0.10, 1000, 0.10, 1000)
    assert z == 0 and p == pytest.approx(1.0)
    assert proportion_z(0.0, 5000, 0.1, 500)[1] < 1e-10
    assert js_divergence([1, 0], [0, 1]) == pytest.approx(1.0, abs=1e-6)
    assert js_divergence([1, 1], [1, 1]) == pytest.approx(0.0, abs=1e-9)
    assert math.isfinite(js_divergence([0, 0, 1], [1, 0, 0]))


def test_monitor_requires_persistence_before_alerting():
    rng = np.random.default_rng(3)
    mon = Monitor(make_data(rng, N_REF), Policy(persistence=2))
    drifted = make_data(rng, N_WIN, "mean_shift", 1.0)
    first = mon.check_window(drifted)
    assert first["age"]["flagged"] and not first["age"]["alert"]
    assert mon.check_window(make_data(rng, N_WIN, "mean_shift", 1.0))["age"]["alert"]
    assert not mon.check_window(make_data(rng, N_WIN))["age"]["alert"]  # recovery resets the streak


def test_stable_streams_do_not_alert_under_the_default_policy():
    r = study(15, None, 0.0)
    assert r["detection_rate"] == 0.0  # no drift injected: any alert is a false alarm


def test_loose_policy_does_raise_false_alarms():
    assert study(15, None, 0.0, policy=Policy(alpha=0.2, persistence=1))["detection_rate"] > 0.2


@pytest.mark.parametrize(
    "drift,size",
    [
        ("mean_shift", 0.6),
        ("variance_change", 0.6),
        ("categorical_shift", 0.15),
        ("missing_rate", 0.05),
    ],
)
def test_large_injected_drift_is_always_detected_promptly(drift, size):
    r = study(10, drift, size)
    assert r["detection_rate"] == 1.0 and r["median_delay_windows"] <= 1


def test_tiny_drift_is_honestly_missed():
    assert study(10, "mean_shift", 0.1)["detection_rate"] <= 0.2


def test_detection_delay_and_no_pre_onset_alert():
    r = run_stream(7, "mean_shift", 1.0, onset=5)
    assert r["first_alert_delay"] in (0, 1) and r["false_alert_windows"] == 0
