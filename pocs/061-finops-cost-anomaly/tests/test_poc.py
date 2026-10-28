import numpy as np
import pytest
from finops_cost_anomaly.detectors import (
    DETECTORS,
    dod_threshold,
    ewma,
    incidents,
    rolling_z,
    seasonal_mad,
)
from finops_cost_anomaly.evaluate import attribute, budget_breach, forecast_month_end, score
from finops_cost_anomaly.generate import Anomaly, baseline, by, default_anomalies, inject, total


def flat(n=120, level=100.0, seed=0, noise=0.03):
    rng = np.random.default_rng(seed)
    return level * np.exp(rng.normal(0, noise, n))


def test_generator_is_deterministic_and_positive():
    a, b = baseline(np.random.default_rng(1)), baseline(np.random.default_rng(1))
    assert all(np.array_equal(a[k], b[k]) for k in a) and all((v > 0).all() for v in a.values())
    assert len(a) == 24 and total(a).shape == (180,)


def test_injection_changes_only_the_labelled_series_and_window():
    base = baseline(np.random.default_rng(2))
    inj = inject(base, [Anomaly("spike", 50, 2, "EC2", "dev", 5.0)])
    changed = {k for k in base if not np.array_equal(base[k], inj[k])}
    assert changed == {("EC2", "dev")}
    diff = np.flatnonzero(inj[("EC2", "dev")] != base[("EC2", "dev")])
    assert list(diff) == [50, 51] and np.allclose(
        inj[("EC2", "dev")][50] / base[("EC2", "dev")][50], 5.0
    )


def test_new_service_adds_a_series_and_ramp_grows():
    base = baseline(np.random.default_rng(3))
    inj = inject(
        base,
        [
            Anomaly("new_service", 100, 10, "Bedrock", "dev", 40.0),
            Anomaly("ramp", 20, 10, "S3", "prod", 2.0),
        ],
    )
    assert ("Bedrock", "dev") in inj and inj[("Bedrock", "dev")][100:110].sum() == pytest.approx(
        400
    )
    r = inj[("S3", "prod")][20:30] / base[("S3", "prod")][20:30]
    assert r[0] == pytest.approx(1.0) and r[-1] == pytest.approx(2.0) and (np.diff(r) > 0).all()


def test_incident_merging():
    f = np.zeros(30, bool)
    f[[3, 4, 6, 20]] = True
    assert incidents(f, gap=2) == [(3, 6), (20, 20)]
    assert incidents(f, gap=0) == [(3, 4), (6, 6), (20, 20)]
    assert incidents(np.zeros(5, bool)) == []


@pytest.mark.parametrize("name", list(DETECTORS))
def test_every_detector_flags_a_5x_spike_and_is_quiet_on_flat_noise(name):
    x = flat(120)
    assert (
        not DETECTORS[name](x)[28:].any() or name == "day_over_day_30pct"
    )  # naive baseline may fire on noise spikes
    y = x.copy()
    y[80] *= 5
    assert DETECTORS[name](y)[80]


def test_flat_noise_false_alarm_ceiling():
    assert not rolling_z(flat(200, seed=4)).any()
    assert ewma(flat(200, seed=5)).sum() <= 2


def test_adaptive_detectors_miss_what_a_static_baseline_sees_after_adapting():
    x = flat(200)
    x[100:] *= 1.5  # persistent step
    z = rolling_z(x)
    assert (
        z[100] and not z[140:].any()
    )  # rolling window absorbs the new level: flagged only at the start
    assert ewma(x)[100:130].any()


def test_weekly_pattern_does_not_trigger_seasonal_detector():
    t = np.arange(150)
    x = (
        100
        * np.array([1.05, 1.08, 1.08, 1.06, 1.02, 0.85, 0.82])[t % 7]
        * np.exp(np.random.default_rng(6).normal(0, 0.02, 150))
    )
    assert seasonal_mad(x).sum() == 0
    assert (
        dod_threshold(x, 0.40).sum() == 0 and dod_threshold(x, 0.10).sum() > 0
    )  # weekday swings fool a day-over-day rule at 10%


def test_score_hand_computed():
    truth = [Anomaly("spike", 50, 2, "EC2", "dev", 5), Anomaly("spike", 90, 1, "S3", "dev", 5)]
    r = score([(51, 51), (120, 121)], truth, tol=2)  # one hit, one miss, one false incident
    assert (r["precision"], r["recall"], r["incidents"]) == (0.5, 0.5, 2) and r["caught"] == [
        True,
        False,
    ]
    assert r["median_delay_days"] == 1.0
    assert score([], truth)["recall"] == 0 and score([], [])["recall"] == 1.0


def test_attribution_finds_the_injected_series():
    base = baseline(np.random.default_rng(7))
    inj = inject(base, [Anomaly("spike", 100, 1, "Athena", "prod", 12.0)])
    a = attribute(inj, 100)
    assert (
        a["top"] == ("Athena", "prod")
        and a["by_service"] == "Athena"
        and a["share_of_excess"] > 0.5
    )
    assert by(inj, 0)["Athena"][100] > by(base, 0)["Athena"][100]


def test_forecast_and_breach_day():
    daily = np.full(90, 100.0)
    assert forecast_month_end(daily[:70], 10, 30) == pytest.approx(3000)
    assert budget_breach(daily, 2900, 60) == 1
    assert budget_breach(daily, 3100, 60) is None
    d2 = daily.copy()
    d2[70:] = 200
    day = budget_breach(d2, 3300, 60)
    assert day is not None and day >= 10  # forecast crosses only after the doubling is visible


def test_full_pipeline_beats_naive_baseline_on_precision():
    from finops_cost_anomaly.main import detect_on_dimensions, run_seed

    _, series, truth = run_seed(0)
    naive = score(detect_on_dimensions(series, dod_threshold), truth)
    good = score(detect_on_dimensions(series, ewma), truth)
    assert good["recall"] >= 0.85 and good["precision"] > naive["precision"] + 0.2
    assert len(default_anomalies()) == 8
