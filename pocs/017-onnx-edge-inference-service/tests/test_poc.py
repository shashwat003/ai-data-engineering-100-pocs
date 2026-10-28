import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient
from onnx_edge_inference_service.bench import run as bench
from onnx_edge_inference_service.engine import MAX_BATCH, InputError, OnnxModel, validate
from onnx_edge_inference_service.model import N_FEATURES, make_data
from onnx_edge_inference_service.service import SloTracker, create_app

R = bench(__import__("tempfile").mkdtemp(), n_lat=30)
V = R["variants"]


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    from onnx_edge_inference_service.model import export, train

    x, y = make_data(2000)
    return OnnxModel(export(train(x, y), tmp_path_factory.mktemp("m"))["int8"])


@pytest.fixture
def client(model):
    return TestClient(create_app(model, p99_budget_ms=50))


def test_onnx_fp32_matches_sklearn_numerically():
    assert (
        V["onnx fp32"]["max_abs_diff_vs_sklearn"] < 1e-4
        and V["onnx fp32"]["agreement_with_sklearn"] == 1.0
    )


def test_int8_is_much_smaller_and_costs_little_accuracy():
    assert V["onnx int8 (dynamic)"]["size_kb"] < 0.5 * V["onnx fp32"]["size_kb"]
    assert V["onnx fp32"]["auc"] - V["onnx int8 (dynamic)"]["auc"] < 0.01
    assert V["onnx int8 (dynamic)"]["agreement_with_sklearn"] > 0.97


def test_latencies_are_recorded_and_ordered():
    for v in V.values():
        assert 0 < v["batch1"]["p50_ms"] <= v["batch1"]["p95_ms"] <= v["batch1"]["p99_ms"]


@pytest.mark.parametrize(
    "bad",
    [
        [[0.0] * 19],
        [[0.0] * 21],
        [[float("nan")] + [0.0] * 19],
        [[float("inf")] + [0.0] * 19],
        [[1e6] + [0.0] * 19],
        [],
        [[0.0] * 20] * (MAX_BATCH + 1),
        [["a"] * 20],
        [0.0] * 20,
        None,
    ],
)
def test_validate_rejects_bad_input(bad):
    with pytest.raises(InputError):
        validate(bad)


def test_validate_accepts_boundary_batch():
    assert validate(np.zeros((MAX_BATCH, N_FEATURES))).shape == (MAX_BATCH, N_FEATURES)


def test_health_predict_and_metrics(client):
    assert client.get("/health").json()["n_features"] == N_FEATURES
    r = client.post("/predict", json={"features": [[0.1] * 20, [-0.3] * 20]})
    assert r.status_code == 200 and len(r.json()["probabilities"]) == 2
    assert all(0 <= p <= 1 for p in r.json()["probabilities"]) and set(r.json()["label"]) <= {0, 1}
    m = client.get("/metrics").json()
    assert m["n"] == 1 and m["slo_met"] is True


@pytest.mark.parametrize(
    "body",
    [
        {"features": [[0.0] * 19]},
        {"features": []},
        {"features": "x"},
        {},
        {"features": [[0.0] * 20] * (MAX_BATCH + 1)},
    ],
)
def test_api_returns_422_for_bad_payloads(client, body):
    assert client.post("/predict", json=body).status_code == 422


def test_non_finite_json_is_rejected(client):
    r = client.post(
        "/predict",
        content='{"features": [[NaN, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]]}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code in (400, 422)


def test_internal_errors_do_not_leak_details(model):
    class Broken:
        path = model.path

        def predict_proba(self, x):
            raise RuntimeError("secret path /etc/passwd")

    c = TestClient(create_app(Broken()), raise_server_exceptions=False)
    r = c.post("/predict", json={"features": [[0.0] * 20]})
    assert (
        r.status_code == 500 and "secret" not in r.text and r.json() == {"detail": "internal error"}
    )


def test_slo_tracker_semantics():
    t = SloTracker(10.0, window=100)
    assert t.snapshot()["slo_met"] is None
    for _ in range(99):
        t.record(1.0)
    t.record(50.0)
    assert t.snapshot()["p50_ms"] == 1.0
    for _ in range(5):
        t.record(50.0)
    assert t.snapshot()["slo_met"] is False


def test_slo_window_forgets_old_samples():
    t = SloTracker(10.0, window=10)
    for _ in range(10):
        t.record(100.0)
    for _ in range(10):
        t.record(1.0)
    assert t.snapshot()["slo_met"] is True


def test_concurrent_requests_give_identical_answers(model):
    x = np.random.default_rng(0).normal(size=(1, N_FEATURES))
    want = model.predict_proba(x)[0]
    got = []
    ts = [threading.Thread(target=lambda: got.append(model.predict_proba(x)[0])) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(got) == 8 and all(abs(g - want) < 1e-9 for g in got)
