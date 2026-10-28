"""POC 017: sklearn -> ONNX -> int8 on CPU; accuracy, size, latency and an SLO-tracked HTTP service."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from pocs_common import write_json
from pocs_common.report import markdown_table

from .bench import run as bench
from .engine import OnnxModel
from .model import make_data
from .service import create_app

POC_DIR = Path(__file__).resolve().parent.parent


def http_load(model_path, n: int = 400, budget_ms: float = 50.0) -> dict:
    x, _ = make_data(200, seed=9)
    client = TestClient(create_app(OnnxModel(model_path), budget_ms))
    for i in range(n):
        r = client.post("/predict", json={"features": [x[i % 200].tolist()]})
        assert r.status_code == 200
    return client.get("/metrics").json()


def main() -> None:
    with tempfile.TemporaryDirectory() as t:
        res = bench(t)
        res["http"] = http_load(Path(t) / "model_int8.onnx")
    write_json(POC_DIR / "results" / "results.json", res)
    rows = [
        (
            n,
            v["accuracy"],
            v["auc"],
            v["max_abs_diff_vs_sklearn"],
            v["size_kb"] or "-",
            v["batch1"]["p50_ms"],
            v["batch1"]["p99_ms"],
            v["batch32"]["p50_ms"],
        )
        for n, v in res["variants"].items()
    ]
    h = res["http"]
    md = (
        "# Results: MLP served on CPU through ONNX Runtime (one thread)\n\n"
        f"Synthetic 20-feature binary task, {res['test_rows']} held-out rows; latencies are wall-clock on this machine and vary run to run.\n\n"
        + markdown_table(
            [
                "variant",
                "accuracy",
                "AUC",
                "max abs diff vs sklearn",
                "size KB",
                "batch=1 p50 ms",
                "batch=1 p99 ms",
                "batch=32 p50 ms",
            ],
            rows,
        )
        + f"\n\nHTTP service (in-process TestClient, int8 model, {h['n']} single-row requests): p50 {h['p50_ms']} ms, p95 {h['p95_ms']} ms, "
        f"p99 {h['p99_ms']} ms against a {h['budget_p99_ms']} ms p99 budget: SLO met = {h['slo_met']}. This excludes network and process-boundary cost.\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
