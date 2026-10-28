import os

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from mlflow_registry_promotion.registry import (  # noqa: E402
    MODEL_NAME,
    Gate,
    evaluate_gate,
    load,
    make_data,
    promote,
    rollback,
    setup,
    train_and_register,
)
from sklearn.svm import LinearSVC  # noqa: E402

X, y, g = make_data(5000, 1)
Xh, yh, gh = make_data(5000, 2)


@pytest.fixture
def client(tmp_path):
    return setup(tmp_path)


def champion(c):
    return c.get_model_version_by_alias(MODEL_NAME, "champion").version


def test_first_model_is_promotable_and_gets_audit_tags(client):
    v = train_and_register(client, X[:40], y[:40], C=1.0, seed=0, note="first")
    d = evaluate_gate(client, v, Xh, yh, gh)
    assert d.promote and "no champion yet" in d.reasons[0]
    promote(client, v, "alice", "first model")
    mv = client.get_model_version(MODEL_NAME, v)
    assert (
        champion(client) == v
        and mv.tags["promoted_by"] == "alice"
        and mv.tags["promotion_reason"] == "first model"
    )


def test_clearly_better_model_is_promoted_and_previous_alias_set(client):
    v1 = train_and_register(client, X[:40], y[:40], C=1.0, seed=0)
    promote(client, v1, "bot", "baseline")
    v2 = train_and_register(client, X, y, C=1.0, seed=0)
    d = evaluate_gate(client, v2, Xh, yh, gh)
    assert d.promote and d.delta_ci[0] > 0.002
    promote(client, v2, "bot", d.reasons[0])
    assert (
        champion(client) == v2
        and client.get_model_version_by_alias(MODEL_NAME, "previous").version == v1
    )


def test_equal_or_worse_model_is_rejected_with_a_statistical_reason(client):
    v1 = train_and_register(client, X, y, C=1.0, seed=0)
    promote(client, v1, "bot", "x")
    same = train_and_register(client, X, y, C=1.0, seed=1)
    d = evaluate_gate(client, same, Xh, yh, gh)
    assert (
        not d.promote
        and "not demonstrated" in d.reasons[0]
        and d.delta_ci == pytest.approx((0.0, 0.0), abs=1e-9)
    )
    worse = train_and_register(client, X[:30], y[:30], C=1.0, seed=0)
    d2 = evaluate_gate(client, worse, Xh, yh, gh)
    assert not d2.promote and d2.delta_ci[1] < 0


def test_signature_mismatch_is_caught_before_any_scoring(client):
    v = train_and_register(client, X, y, C=1.0, seed=0, drop_feature=2)
    d = evaluate_gate(client, v, Xh, yh, gh)
    assert not d.promote and "signature mismatch" in d.reasons[0]


def test_smoke_inference_failure_blocks_promotion(client):
    import pickle
    from pathlib import Path

    v = train_and_register(client, X, y, C=1.0, seed=0)
    mv = client.get_model_version(MODEL_NAME, v)
    Path(mv.source.replace("file://", "")).joinpath("model.pkl").write_bytes(
        pickle.dumps(LinearSVC().fit(X[:200], y[:200]))
    )  # no predict_proba
    d = evaluate_gate(client, v, Xh, yh, gh)
    assert not d.promote and "smoke inference failed" in d.reasons[0]


def test_subgroup_regression_is_reported_even_for_an_otherwise_acceptable_gate(client):
    v1 = train_and_register(client, X, y, C=1.0, seed=0)
    promote(client, v1, "bot", "x")
    v2 = train_and_register(client, X[g == 0], y[g == 0], C=1.0, seed=0)
    d = evaluate_gate(client, v2, Xh, yh, gh, Gate(min_delta=-1.0, max_subgroup_drop=0.0001))
    assert not d.promote and any("subgroup" in r for r in d.reasons)


def test_rollback_swaps_champion_and_previous_and_is_recorded(client):
    v1 = train_and_register(client, X[:40], y[:40], C=1.0, seed=0)
    promote(client, v1, "bot", "a")
    v2 = train_and_register(client, X, y, C=1.0, seed=0)
    promote(client, v2, "bot", "b")
    assert rollback(client, "oncall") == v1 and champion(client) == v1
    assert client.get_model_version_by_alias(MODEL_NAME, "previous").version == v2
    assert client.get_model_version(MODEL_NAME, v1).tags["rolled_back_by"] == "oncall"
    assert rollback(client, "oncall") == v2  # and back again


def test_rollback_without_history_fails_clearly(client):
    v1 = train_and_register(client, X[:40], y[:40], C=1.0, seed=0)
    promote(client, v1, "bot", "a")
    with pytest.raises(Exception, match="previous"):
        rollback(client, "oncall")


def test_repromoting_the_current_champion_keeps_the_rollback_target(client):
    v1 = train_and_register(client, X[:40], y[:40], C=1.0, seed=0)
    promote(client, v1, "bot", "a")
    v2 = train_and_register(client, X, y, C=1.0, seed=0)
    promote(client, v2, "bot", "b")
    promote(client, v2, "bot", "again")
    assert client.get_model_version_by_alias(MODEL_NAME, "previous").version == v1


def test_registry_persists_runs_params_and_loads_the_exact_model(client):
    v = train_and_register(client, X, y, C=0.5, seed=3, note="n")
    model, sig = load(client, v)
    mv = client.get_model_version(MODEL_NAME, v)
    run = client.get_run(mv.run_id)
    assert (
        run.data.params["C"] == "0.5"
        and run.data.params["seed"] == "3"
        and run.data.metrics["train_auc"] > 0.9
    )
    assert sig == {"n_features": 6} and np.isfinite(model.predict_proba(Xh)[:, 1]).all()
