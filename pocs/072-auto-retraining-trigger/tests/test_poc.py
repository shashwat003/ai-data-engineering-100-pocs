import os

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from auto_retraining_trigger.sim import LABEL_DELAY, POLICIES, WEEKS, Policy, make_week, run  # noqa: E402

FIXED = Policy("fixed", every=8, cooldown=8, max_per_quarter=9)


def test_world_is_deterministic_and_actually_drifts():
    a, b = make_week(np.random.default_rng(1), 5), make_week(np.random.default_rng(1), 5)
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])
    early = make_week(np.random.default_rng(2), 3)[0].mean(0)[0]
    late = make_week(np.random.default_rng(2), 40)[0].mean(0)[0]
    assert late - early > 1.0  # covariate shift at week 35


def test_never_retraining_has_no_retrains_and_degrades_after_the_concept_flip():
    r = run(None, 1)
    assert r["retrains"] == 0 and len(r["aucs"]) == WEEKS - 4
    pre, post = np.mean(r["aucs"][:14]), np.mean(r["aucs"][20:])
    assert post < pre - 0.08


def test_fixed_schedule_retrains_exactly_on_schedule():
    r = run(FIXED, 1)
    assert r["retrain_weeks"] == [11, 19, 27, 35, 43, 51]
    assert np.diff(r["retrain_weeks"]).tolist() == [8] * 5


def test_any_retraining_beats_none_on_average():
    base = np.mean([run(None, s)["mean_auc"] for s in range(4)])
    for pol in POLICIES[1:]:
        assert np.mean([run(pol, s)["mean_auc"] for s in range(4)]) > base + 0.04, pol.name


def test_cooldown_and_quarterly_budget_are_respected():
    pol = Policy("eager", every=1, cooldown=3, max_per_quarter=2)
    for seed in range(3):
        w = run(pol, seed)["retrain_weeks"]
        assert all(b - a >= 3 for a, b in zip(w, w[1:], strict=False))
        assert all(sum(1 for x in w if t <= x < t + 13) <= 2 for t in range(WEEKS))


def test_drift_trigger_is_quiet_before_the_covariate_shift_and_fires_after_it():
    pol = Policy("drift", drift=True)
    early, late = 0, 0
    for seed in range(6):
        w = run(pol, seed)["retrain_weeks"]
        early += sum(x < 35 for x in w)
        late += sum(x >= 35 for x in w)
    assert (
        early <= 3 and late >= 5
    )  # near-zero false alarms on stationary inputs, reacts to the shift


def test_labels_arrive_late_so_performance_trigger_cannot_fire_instantly():
    pol = Policy("perf", perf=0.92, cooldown=1, max_per_quarter=9)
    w = run(pol, 0)["retrain_weeks"]
    assert w and min(w) >= 20 + LABEL_DELAY  # no earlier than the concept flip plus the label delay


def test_gate_rejects_candidates_trained_on_corrupted_labels_and_protects_quality():
    kw = dict(every=6, cooldown=6, max_per_quarter=9)
    bad = [run(Policy("x", gate=False, **kw), s, corrupt_training_labels_from=26) for s in range(4)]
    good = [run(Policy("x", gate=True, **kw), s, corrupt_training_labels_from=26) for s in range(4)]
    assert np.mean([r["rejected"] for r in good]) >= 2 and sum(r["rejected"] for r in bad) == 0
    assert np.mean([r["mean_auc"] for r in good]) > np.mean([r["mean_auc"] for r in bad]) + 0.03


def test_policy_table_and_naming():
    assert POLICIES[0] is None and len({p.name for p in POLICIES[1:]}) == 4


def test_runs_are_reproducible():
    assert run(FIXED, 3)["aucs"] == run(FIXED, 3)["aucs"]


def test_promotion_gate_from_poc_065_composes_with_the_retrain_loop(tmp_path):
    from mlflow_registry_promotion.registry import (
        MODEL_NAME,
        evaluate_gate,
        make_data,
        promote,
        setup,
        train_and_register,
    )

    X, y, g = make_data(3000, 1)
    Xh, yh, gh = make_data(3000, 2)
    c = setup(tmp_path)
    v1 = train_and_register(c, X[:40], y[:40], C=1.0, seed=0)
    promote(c, v1, "retrain-bot", "initial")
    v2 = train_and_register(c, X, y, C=1.0, seed=0)  # the "retrained" candidate
    d = evaluate_gate(c, v2, Xh, yh, gh)
    assert d.promote
    promote(c, v2, "retrain-bot", d.reasons[0])
    assert c.get_model_version_by_alias(MODEL_NAME, "champion").version == v2


@pytest.mark.parametrize("bad", [dict(every=0), dict(cooldown=0)])
def test_degenerate_policies_do_not_crash(bad):
    assert "mean_auc" in run(Policy("odd", **bad), 0)
