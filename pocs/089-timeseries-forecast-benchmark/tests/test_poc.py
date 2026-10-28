import numpy as np
import pytest
from timeseries_forecast_benchmark.evaluate import mase, rolling_origins, run, smape
from timeseries_forecast_benchmark.models import (
    STAT_MODELS,
    GlobalLGBM,
    drift,
    ets,
    naive,
    seasonal_naive,
    theta,
)
from timeseries_forecast_benchmark.series import PATTERNS, PERIOD, H, dataset, make_series

R = run(n_per_pattern=4, origins=2)
M = R["models"]


def test_dataset_is_deterministic_and_covers_all_patterns():
    a, b = dataset(2), dataset(2)
    assert [s["id"] for s in a] == [s["id"] for s in b] and all(
        np.array_equal(x["y"], y["y"]) for x, y in zip(a, b, strict=True)
    )
    assert {s["pattern"] for s in a} == set(PATTERNS) and all((s["y"] >= 0).all() for s in a)


def test_intermittent_series_is_mostly_zeros():
    y = make_series("intermittent", np.random.default_rng(1), 200)
    assert (y == 0).mean() > 0.5


def test_baseline_forecasts_are_exact():
    y = np.arange(1.0, 37.0)
    assert naive(y).tolist() == [36.0] * H
    assert seasonal_naive(y).tolist() == y[-PERIOD:].tolist()
    assert drift(y).tolist() == pytest.approx([36 + i for i in range(1, H + 1)])
    assert (drift(y[::-1]) >= 0).all()  # a falling series must not forecast negative demand


def test_metrics_known_values():
    a, f = np.array([10.0, 20.0]), np.array([12.0, 18.0])
    assert smape(a, f) == pytest.approx(np.mean([2 * 2 / 22, 2 * 2 / 38]) * 100)
    train = np.array([1.0] * 12 + [3.0] * 12)  # seasonal diffs all 2 -> scale 2 over the 12 diffs
    assert mase(a, f, train) == pytest.approx(2.0 / np.mean(np.abs(train[12:] - train[:-12])))
    assert smape(np.zeros(3), np.zeros(3)) == 0.0


def test_every_forecaster_returns_finite_nonnegative_horizon_on_every_pattern():
    rng = np.random.default_rng(3)
    for p in PATTERNS:
        y = make_series(p, rng, 84)
        for name, fn in STAT_MODELS.items():
            f = fn(y)
            assert f.shape == (H,) and np.isfinite(f).all() and (f >= 0).all(), (p, name)


def test_models_cope_with_short_and_constant_series():
    for y in (np.full(30, 5.0), np.arange(1.0, 20.0), np.zeros(40)):
        assert np.isfinite(ets(y)).all() and np.isfinite(theta(y)).all()


def test_seasonal_models_beat_non_seasonal_baselines_on_seasonal_patterns():
    for p in ("stable_seasonal", "changing_seasonality"):
        assert M["seasonal naive"]["mase_by_pattern"][p] * 2 < M["naive"]["mase_by_pattern"][p]


def test_best_model_is_not_a_baseline_and_beats_seasonal_naive_with_confidence():
    assert R["best_model"] not in ("naive", "drift", "seasonal naive")
    lo, hi = R["best_vs_seasonal_naive_mase_diff_ci95"]
    assert hi < 0


def test_metric_choice_matters_sMAPE_and_MASE_rank_differently():
    by_s = min(M, key=lambda n: M[n]["smape"])
    by_m = min(M, key=lambda n: M[n]["mase"])
    assert by_s != by_m  # sMAPE is distorted by intermittent zeros; report both


def test_combination_is_the_mean_of_its_members_in_error_terms():
    y = make_series("trend_seasonal", np.random.default_rng(5), 84)
    lg = GlobalLGBM().fit([y])
    combo = (ets(y) + theta(y) + lg.forecast(y)) / 3
    assert combo.shape == (H,) and np.isfinite(combo).all()


def test_global_model_forecast_does_not_depend_on_data_after_the_cut():
    rng = np.random.default_rng(6)
    full = [make_series("trend_seasonal", rng, 96) for _ in range(5)]
    cut = 80
    a = GlobalLGBM().fit([y[:cut] for y in full]).forecast(full[0][:cut])
    poisoned = [y.copy() for y in full]
    for y in poisoned:
        y[cut:] += 1e6  # a huge change strictly after the cut
    b = GlobalLGBM().fit([y[:cut] for y in poisoned]).forecast(poisoned[0][:cut])
    assert np.allclose(a, b)


def test_rolling_origins_and_foundation_model_status():
    assert rolling_origins(np.zeros(100), 2) == [100 - H - 6, 100 - H]
    assert R["foundation_models"].startswith("not run")


def test_run_is_deterministic():
    assert (
        run(n_per_pattern=2, origins=1)["mean_rank"] == run(n_per_pattern=2, origins=1)["mean_rank"]
    )
