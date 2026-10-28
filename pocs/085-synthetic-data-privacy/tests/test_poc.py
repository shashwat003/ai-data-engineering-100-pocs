import numpy as np
import pandas as pd
import pytest
from synthetic_data_privacy.data import CAT, LABEL, NUM, make_real
from synthetic_data_privacy.main import run
from synthetic_data_privacy.metrics import fidelity, privacy
from synthetic_data_privacy.synth import Marginal, Synth

R = run(n_train=1200, n_holdout=600, eps_list=(0.1, 10.0))
BY = {(r["kind"], r["eps"]): r for r in R["rows"]}
IND, COP, MEM = BY[("independent", None)], BY[("copula", None)], BY[("memoriser", None)]


def test_copula_preserves_correlations_far_better_than_independent_marginals():
    assert COP["corr_abs_diff"] < IND["corr_abs_diff"] / 3
    assert COP["corr_abs_diff"] < 0.08


def test_marginals_are_close_for_all_synthesizers():
    for r in (IND, COP):
        assert r["ks_mean"] < 0.06 and r["tvd_mean"] < 0.06


def test_copula_has_more_utility_than_independent():
    assert COP["tstr_auc"] > IND["tstr_auc"]


def test_memoriser_is_caught_by_distance_metrics_even_though_exact_copy_rate_is_zero():
    assert MEM["exact_copy_rate"] == 0.0  # jitter defeats a naive duplicate check
    assert MEM["dcr_ratio"] < 0.3 and MEM["mia_auc"] > 0.7


def test_copula_leaks_little():
    assert 0.85 < COP["dcr_ratio"] < 1.15 and COP["mia_auc"] < 0.58


def test_small_epsilon_degrades_marginals_and_large_epsilon_recovers():
    lo, hi = BY[("copula", 0.1)], BY[("copula", 10.0)]
    assert lo["ks_mean"] > hi["ks_mean"]
    assert abs(hi["ks_mean"] - COP["ks_mean"]) < 0.02


def test_run_is_reproducible():
    assert run(n_train=300, n_holdout=150, eps_list=(1.0,)) == run(
        n_train=300, n_holdout=150, eps_list=(1.0,)
    )


def test_sample_respects_schema_and_domains():
    real = make_real(800)
    s = Synth("copula", NUM, CAT, LABEL, seed=1).fit(real).sample(500)
    assert list(s.columns) == NUM + CAT + [LABEL] and len(s) == 500
    assert set(s[LABEL]) <= {0, 1} and set(s["region"]) <= set(real["region"])
    assert s["age"].between(real["age"].min(), real["age"].max()).all()
    assert s.isna().sum().sum() == 0


def test_laplace_noise_is_calibrated_to_histogram_sensitivity():
    s = pd.Series(np.zeros(1000))
    s.iloc[0] = 1.0  # two occupied bins; others are empty so only noise lands there
    devs = []
    for seed in range(200):
        m = Marginal(s, True, 1.0, np.random.default_rng(seed), bins=10)
        devs.append(m.p[5])  # an empty bin: probability mass from noise alone
    assert 0 < np.mean(devs) < 0.05


@pytest.mark.parametrize("bad", [0, -1])
def test_invalid_epsilon_rejected(bad):
    with pytest.raises(ValueError, match="epsilon"):
        Synth("copula", NUM, CAT, LABEL, eps=bad)


def test_bad_inputs_rejected():
    with pytest.raises(ValueError, match="unknown"):
        Synth("magic", NUM, CAT, LABEL)
    real = make_real(100)
    with pytest.raises(ValueError, match="missing"):
        Synth("copula", NUM, CAT, LABEL).fit(real.drop(columns=["plan"]))
    with pytest.raises(ValueError, match="at least"):
        Synth("copula", NUM, CAT, LABEL).fit(real.head(5))


def test_privacy_metric_detects_a_verbatim_copy():
    real = make_real(600)
    train, hold = real.iloc[:300].reset_index(drop=True), real.iloc[300:].reset_index(drop=True)
    p = privacy(train, hold, train.copy(), NUM, CAT)
    assert p["exact_copy_rate"] == 1.0 and p["mia_auc"] > 0.9


def test_fidelity_of_identical_data_is_perfect():
    real = make_real(400)
    f = fidelity(real, real.copy(), NUM, CAT)
    assert f == {"ks_mean": 0.0, "tvd_mean": 0.0, "corr_abs_diff": 0.0}
