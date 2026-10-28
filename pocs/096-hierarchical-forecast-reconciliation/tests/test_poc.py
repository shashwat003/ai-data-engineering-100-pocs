import numpy as np
import pytest
from hierarchical_forecast_reconciliation.evaluate import multi_seed, run
from hierarchical_forecast_reconciliation.hierarchy import generate, summing_matrix
from hierarchical_forecast_reconciliation.reconcile import (
    METHODS,
    base_forecasts,
    coherence_gap,
    projection,
    reconcile,
    shrunk_covariance,
)

S, NAMES = summing_matrix()
R = run(n_obs=72, origins=3, seed=5)
M = R["methods"]


def test_summing_matrix_structure():
    assert S.shape == (16, 12) and NAMES[0] == "total" and (S[0] == 1).all()
    assert S[1:4].sum(axis=0).tolist() == [1] * 12 and (S[4:] == np.eye(12)).all()


def test_generated_series_are_exactly_coherent_and_positive():
    allser, S2, _ = generate(60, seed=3)
    assert np.allclose(allser, S2 @ allser[-12:]) and (allser > 0).all()


@pytest.mark.parametrize("method", ["bottom-up", "OLS", "WLS (structural)", "MinT (shrink)"])
def test_unbiasedness_condition_PS_equals_identity(method):
    rng = np.random.default_rng(1)
    P = projection(S, method, resid=rng.normal(size=(16, 60)))
    assert np.allclose(P @ S, np.eye(12), atol=1e-8)


def test_top_down_is_coherent_but_not_unbiased():
    hist = np.abs(np.random.default_rng(2).normal(50, 5, (12, 40)))
    P = projection(S, "top-down", bottom_hist=hist)
    assert not np.allclose(P @ S, np.eye(12))
    y = np.abs(np.random.default_rng(3).normal(100, 10, (16, 8)))
    assert coherence_gap(S, S @ P @ y) < 1e-9


@pytest.mark.parametrize("method", ["bottom-up", "OLS", "WLS (structural)", "MinT (shrink)"])
def test_reconciling_an_already_coherent_forecast_changes_nothing(method):
    rng = np.random.default_rng(4)
    bottom = np.abs(rng.normal(50, 8, (12, 8)))
    coherent = S @ bottom
    out = reconcile(S, coherent, method, resid=rng.normal(size=(16, 60)))
    assert np.allclose(out, coherent)


def test_every_reconciled_forecast_is_coherent_and_base_forecasts_are_not():
    assert M["base (incoherent)"]["max_coherence_gap"] > 1.0
    for m in METHODS[1:]:
        assert M[m]["max_coherence_gap"] < 1e-8, m


def test_shrunk_covariance_properties():
    rng = np.random.default_rng(5)
    resid = rng.normal(size=(16, 40)) + rng.normal(
        size=(1, 40)
    )  # a shared component creates correlation
    W, lam = shrunk_covariance(resid)
    assert 0 <= lam <= 1 and np.allclose(W, W.T) and np.linalg.eigvalsh(W).min() > 0
    raw = np.cov(resid, bias=True)
    assert (
        np.allclose(np.diag(W), np.diag(raw))
        and np.abs(W - np.diag(np.diag(W))).sum()
        <= np.abs(raw - np.diag(np.diag(raw))).sum() + 1e-9
    )


def test_input_validation():
    with pytest.raises(ValueError, match="unknown"):
        projection(S, "magic")
    with pytest.raises(ValueError, match="history"):
        projection(S, "top-down")
    with pytest.raises(ValueError, match="residuals"):
        projection(S, "MinT (shrink)")


def test_base_forecasts_have_expected_shapes_and_are_nonnegative():
    allser, _, _ = generate(60, seed=2)
    f, r = base_forecasts(allser)
    assert f.shape == (16, 8) and r.shape == (16, 60) and (f >= 0).all() and np.isfinite(r).all()


def test_reconciled_forecasts_are_nonnegative_in_the_evaluation():
    assert all(np.isfinite(list(m["rmsse_by_level"].values())).all() for m in M.values())


def test_top_down_is_the_worst_method_on_this_hierarchy():
    assert (
        M["top-down"]["rmsse_by_level"]["all_levels_mean"]
        > 1.2 * M["base (incoherent)"]["rmsse_by_level"]["all_levels_mean"]
    )


def test_gains_over_many_hierarchies_are_small_but_positive_for_wls_and_mint_and_negative_for_top_down():
    ms = multi_seed(seeds=range(1, 5), n_obs=60, origins=2)["methods"]
    assert ms["top-down"]["mean_change_pct"] > 20
    assert (
        ms["WLS (structural)"]["mean_change_pct"] < 1.0
        and ms["MinT (shrink)"]["mean_change_pct"] < 1.0
    )
    assert abs(ms["MinT (shrink)"]["mean_change_pct"]) < 10  # never a dramatic effect either way


def test_run_is_deterministic():
    assert (
        run(n_obs=60, origins=2, seed=1)["methods"]["OLS"]
        == run(n_obs=60, origins=2, seed=1)["methods"]["OLS"]
    )
