import numpy as np
import pytest
from onnx_edge_inference_service.engine import InputError
from reference_edge_cloud_slm.main import outage_scenario
from reference_edge_cloud_slm.router import CircuitBreaker, CloudUnavailable, Costs, HybridRouter
from reference_edge_cloud_slm.stream import window
from reference_edge_cloud_slm.system import (
    build_models,
    calibrate_threshold,
    drift_scenario,
    make_router,
    serve,
)

X = np.zeros(20, dtype=np.float32)


def router(p, cloud=lambda x: 1, threshold=0.8, breaker=None):
    return HybridRouter(
        lambda x: np.array([p]),
        cloud,
        threshold,
        Costs(1.0, 100.0, 400.0, 0.01),
        breaker or CircuitBreaker(),
    )


def test_confident_edge_answer_stays_on_the_edge():
    d = router(0.95).predict(X)
    assert (d.source, d.label, d.cost_usd, d.latency_ms) == ("edge", 1, 0.0, 1.0)


def test_confidence_is_symmetric_around_one_half():
    assert router(0.02).predict(X).source == "edge" and router(0.02).predict(X).label == 0


def test_low_confidence_escalates_and_pays_cloud_cost_and_latency():
    d = router(0.6, cloud=lambda x: 0).predict(X)
    assert (d.source, d.label, d.cost_usd, d.latency_ms) == ("cloud", 0, 0.01, 101.0)


def test_threshold_boundary_is_inclusive_for_the_edge():
    assert router(0.8).predict(X).source == "edge"
    assert router(0.79).predict(X).source == "cloud"


def test_cloud_failure_falls_back_to_the_edge_answer_and_is_flagged():
    def down(x):
        raise CloudUnavailable("t")

    d = router(0.6, cloud=down).predict(X)
    assert (d.source, d.label, d.cost_usd) == ("edge-degraded", 1, 0.0) and d.latency_ms == 401.0


def test_breaker_opens_after_repeated_failures_then_probes_after_cooldown():
    now = {"t": 0.0}
    calls = {"n": 0}

    def down(x):
        calls["n"] += 1
        raise CloudUnavailable("t")

    r = router(0.6, cloud=down, breaker=CircuitBreaker(3, 30.0, lambda: now["t"]))
    for _ in range(10):
        r.predict(X)
    assert calls["n"] == 3  # opened after 3 failures; the next 7 were short-circuited
    assert r.predict(X).latency_ms == 1.0  # degraded without waiting for a timeout
    now["t"] = 31.0
    r.predict(X)
    assert calls["n"] == 4  # half-open probe


def test_breaker_closes_again_after_a_successful_probe():
    now = {"t": 0.0}
    state = {"down": True}

    def cloud(x):
        if state["down"]:
            raise CloudUnavailable("t")
        return 0

    r = router(0.6, cloud=cloud, breaker=CircuitBreaker(2, 10.0, lambda: now["t"]))
    r.predict(X)
    r.predict(X)
    assert r.breaker.open
    state["down"], now["t"] = False, 11.0
    assert r.predict(X).source == "cloud" and not r.breaker.open


def test_force_cloud_overrides_a_confident_edge():
    r = router(0.99)
    r.force_cloud = True
    assert r.predict(X).source == "cloud"


def test_invalid_input_is_rejected_not_silently_routed():
    models = build_models(n_train=1500)
    r = make_router(models, 0.8)
    with pytest.raises(InputError):
        r.predict(np.full(20, np.nan, dtype=np.float32))
    with pytest.raises(InputError):
        r.predict(np.zeros(5, dtype=np.float32))


@pytest.fixture(scope="module")
def models():
    return build_models()


@pytest.fixture(scope="module")
def cal(models):
    return calibrate_threshold(models)


def test_the_cloud_model_is_more_accurate_than_the_edge_model(cal):
    assert cal["cloud_accuracy"] > cal["edge_accuracy"] + 0.02


def test_sweep_accuracy_and_cloud_share_rise_with_the_threshold(cal):
    shares = [s["cloud_share"] for s in cal["sweep"]]
    assert shares == sorted(shares) and shares[0] == 0.0 and shares[-1] > 0.9
    assert cal["sweep"][-1]["accuracy"] > cal["sweep"][0]["accuracy"]


def test_calibrated_threshold_is_within_target_of_cloud_only_on_validation(cal):
    chosen = next(s for s in cal["sweep"] if s["threshold"] == cal["threshold"])
    assert chosen["accuracy"] >= cal["cloud_accuracy"] - 0.01
    assert chosen["cloud_share"] < 0.6  # and it saves a meaningful share of cloud calls


def test_hybrid_beats_edge_only_and_costs_less_than_cloud_only(models, cal):
    x, y = window(np.random.default_rng(5), 800)
    edge = serve(make_router(models, 0.0), x, y)
    hyb = serve(make_router(models, cal["threshold"]), x, y)
    cloud = serve(make_router(models, 1.01), x, y)
    assert hyb["accuracy"] > edge["accuracy"]
    assert (
        hyb["usd_per_1k"] < cloud["usd_per_1k"]
        and hyb["mean_latency_ms"] < cloud["mean_latency_ms"]
    )


def test_circuit_breaker_cuts_outage_latency_without_losing_accuracy(models, cal):
    x, y = window(np.random.default_rng(6), 1000)
    with_b = outage_scenario(models, cal["threshold"], x, y, True)
    without = outage_scenario(models, cal["threshold"], x, y, False)
    assert with_b["mean_latency_ms"] < without["mean_latency_ms"]
    assert with_b["p95_latency_ms"] < without["p95_latency_ms"]
    assert abs(with_b["accuracy"] - without["accuracy"]) < 0.03


def test_drift_monitor_alerts_after_onset_and_stays_quiet_before(models, cal):
    rows = drift_scenario(models, cal["threshold"], 1.0, False, windows=9, onset=4)
    assert all(r["alerting_features"] == 0 for r in rows if not r["drifted"])
    assert any(r["alerting_features"] > 0 for r in rows if r["drifted"])


def test_drift_lowers_edge_only_accuracy(models):
    rng = np.random.default_rng(8)
    clean = serve(make_router(models, 0.0), *window(rng, 3000, 0.0))
    hard = serve(make_router(models, 0.0), *window(rng, 3000, 1.0))
    assert hard["accuracy"] < clean["accuracy"] - 0.005


def test_controller_sends_more_traffic_to_the_cloud_after_an_alert(models, cal):
    base = drift_scenario(models, cal["threshold"], 1.0, False, windows=9, onset=4)
    ctl = drift_scenario(models, cal["threshold"], 1.0, True, windows=9, onset=4)
    post = lambda rows: np.mean([r["cloud_share"] for r in rows if r["drifted"]])  # noqa: E731
    assert post(ctl) > post(base)
    assert [r["cloud_share"] for r in ctl[:4]] == pytest.approx(
        [r["cloud_share"] for r in base[:4]]
    )


def test_streams_are_deterministic():
    a = window(np.random.default_rng(1), 50, 0.5)
    b = window(np.random.default_rng(1), 50, 0.5)
    assert (a[0] == b[0]).all() and (a[1] == b[1]).all()
