import pickle
import random

import pytest
from streaming_anomaly_detection.detectors import (
    AdwinShift,
    EwmaZ,
    Hst,
    MissingRun,
    RobustZ,
    SeasonalEwma,
)
from streaming_anomaly_detection.evaluate import (
    FA_PER_1000,
    FACTORIES,
    alarm_events,
    calibrate,
    match,
    run,
    scores,
)
from streaming_anomaly_detection.streams import PERIOD, Event, make_stream

R = run((1, 2, 3))
D = R["detectors"]


def test_streams_are_deterministic_labelled_and_start_clean():
    a, ea = make_stream(7)
    b, eb = make_stream(7)
    assert a == b and ea == eb
    assert all(e.start >= 2 * PERIOD for e in ea) and {e.kind for e in ea} == {
        "spike",
        "level_shift",
        "variance",
        "dropout",
        "seasonal_break",
    }
    assert all(
        x is None
        for x in a[
            next(e for e in ea if e.kind == "dropout").start : next(
                e for e in ea if e.kind == "dropout"
            ).end
            + 1
        ]
    )
    clean, none = make_stream(7, anomalies=False)
    assert none == [] and sum(x is None for x in clean) == 0


def test_ewma_flags_a_spike_and_ignores_noise():
    d, rng = EwmaZ(), random.Random(1)
    s = [d.update(100 + rng.gauss(0, 4), t) for t in range(500)]
    assert max(s[150:]) < 5.5 and d.update(180.0, 500) > 10 and d.update(None, 501) == 0.0


def test_ewma_does_not_absorb_a_sustained_outlier_quickly():
    d = EwmaZ()
    for t in range(300):
        d.update(100.0 + (t % 3), t)
    scores_ = [d.update(160.0, 300 + i) for i in range(20)]
    assert min(scores_) > 6  # frozen baseline keeps scoring the shift as anomalous


def test_robust_z_ignores_a_single_outlier_in_its_window():
    d = RobustZ()
    for t in range(100):
        d.update(50 + (t % 5), t)
    assert d.update(500, 100) > 20
    assert d.update(52, 101) < 3  # the outlier was not added to the baseline


def test_seasonal_ewma_learns_the_daily_shape_and_flags_a_flat_line():
    xs, _ = make_stream(3, anomalies=False)
    d = SeasonalEwma()
    s = [d.update(x, t) for t, x in enumerate(xs)]
    assert max(s[2 * PERIOD :]) < 6.5
    flat = [
        d.update(100.0, len(xs) + i) for i in range(60)
    ]  # constant value where the pattern should swing
    assert max(flat) > 6


def test_hst_scores_are_bounded_and_adwin_flags_a_level_shift():
    h = Hst()
    xs, _ = make_stream(4, anomalies=False)
    sc = [h.update(x, t) for t, x in enumerate(xs[:1200])]
    assert 0 <= min(sc) and max(sc) <= 1
    a = AdwinShift()
    out = [a.update(x + (30 if t > 3 * PERIOD else 0), t) for t, x in enumerate(xs)]
    assert sum(out[3 * PERIOD :]) >= 1 and sum(out[: 3 * PERIOD]) == 0


def test_missing_run_rule():
    d = MissingRun()
    s = [d.update(None if 700 <= t < 705 else 1.0, t) for t in range(900)]
    assert max(s) == 5.0 and s[705] == 0.0


def test_alarm_events_and_matching():
    assert alarm_events([1, 2, 3, 40, 41, 100], gap=5) == [(1, 3), (40, 41), (100, 100)]
    ev = [Event("spike", 50, 50), Event("level_shift", 200, 230)]
    m = match(ev, [(48, 52), (228, 240), (500, 505)])
    assert (
        list(m["found"].values()) == [True, True]
        and m["false_alarms"] == 1
        and m["delays"] == [0, 28]
    )


def test_thresholds_are_calibrated_on_clean_data_and_generalise_to_other_clean_streams():
    for name in ("EWMA z-score", "Seasonal EWMA", "river HalfSpaceTrees"):
        fac = FACTORIES[name]
        thr = calibrate(fac)
        ev = n = 0
        for seed in (201, 202):
            xs, _ = make_stream(seed, anomalies=False)
            sc = scores(fac, xs)[2 * PERIOD :]
            ev += len(alarm_events([i for i, v in enumerate(sc) if v > thr]))
            n += len(sc)
        assert ev / (n / 1000) <= 4 * FA_PER_1000, name


def test_seasonal_ewma_is_the_best_statistical_detector_here():
    assert D["Seasonal EWMA"]["event_recall"] > max(
        D[n]["event_recall"] for n in ("EWMA z-score", "Rolling median/MAD", "river ADWIN (drift)")
    )
    assert D["Seasonal EWMA"]["event_recall"] >= 0.7


def test_no_single_detector_covers_every_anomaly_type_but_two_together_do():
    for name, d in D.items():
        assert min(d["recall_by_kind"].values()) < 1.0, name
    assert D["Missing-data run rule"]["recall_by_kind"]["dropout"] == 1.0
    covered = {
        k: max(
            D["Seasonal EWMA"]["recall_by_kind"][k], D["Missing-data run rule"]["recall_by_kind"][k]
        )
        for k in D["Seasonal EWMA"]["recall_by_kind"]
    }
    assert all(v == 1.0 for v in covered.values())


def test_streaming_model_state_can_be_snapshotted_and_restored():
    xs, _ = make_stream(5, anomalies=False)
    full = Hst(seed=1)
    a = [full.update(x, t) for t, x in enumerate(xs[:900])]
    half = Hst(seed=1)
    for t, x in enumerate(xs[:450]):
        half.update(x, t)
    restored = pickle.loads(pickle.dumps(half))  # noqa: S301 (our own state; never unpickle untrusted snapshots)
    b = [restored.update(x, t) for t, x in enumerate(xs[450:900], start=450)]
    assert b == pytest.approx(a[450:])


def test_throughput_is_adequate_for_the_slowest_detector():
    assert min(d["points_per_second"] for d in D.values()) > 1000


def test_memory_is_bounded_for_rolling_detectors():
    r = RobustZ(window=100)
    for t in range(10_000):
        r.update(float(t % 7), t)
    assert len(r.buf) == 100
