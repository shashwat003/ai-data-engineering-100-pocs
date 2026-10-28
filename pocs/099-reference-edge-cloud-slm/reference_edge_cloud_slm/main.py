"""POC 099: edge-to-cloud hybrid serving reference. REAL: int8 ONNX edge model, drift monitor, router, breaker.
MODELLED: cloud latency/price. The "SLM" is a small tabular MLP stand-in; no language model is used."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from pocs_common import write_json
from pocs_common.report import markdown_table

from .router import CloudUnavailable, Costs
from .stream import window
from .system import build_models, calibrate_threshold, drift_scenario, make_router, serve

POC_DIR = Path(__file__).resolve().parent.parent


def edge_median_ms(models, x: np.ndarray, n: int = 300) -> float:
    for row in x[:30]:
        models.edge.predict_proba(row.reshape(1, -1))
    t = []
    for row in x[:n]:
        s = time.perf_counter()
        models.edge.predict_proba(row.reshape(1, -1))
        t.append((time.perf_counter() - s) * 1000)
    return float(np.median(t))


def outage_scenario(models, threshold: float, x, y, breaker: bool) -> dict:
    """The cloud is down for requests 300-699 (of the stream). With the breaker the router stops calling after 3 failures
    and probes again every 60 requests."""
    ticks = {"t": 0.0}  # virtual clock: one tick per request

    def flaky(row):
        if 300 <= ticks["t"] < 700:
            raise CloudUnavailable("simulated outage")
        return int(models.cloud.predict(row.reshape(1, -1))[0])

    r = make_router(models, threshold, cloud_fn=flaky)
    base = r.edge_proba

    def counted(x):
        ticks["t"] += 1
        return base(x)

    r.edge_proba = counted
    r.breaker.clock, r.breaker.cooldown_s = (lambda: ticks["t"]), 60.0
    r.breaker.failures_to_open = 3 if breaker else 10**9
    return serve(r, x, y)


def run(quick: bool = False) -> dict:
    models = build_models()
    cal = calibrate_threshold(models)
    th = cal["threshold"]
    rng = np.random.default_rng(99)
    x, y = window(rng, 600 if quick else 1500)
    edge_ms = edge_median_ms(models, x, 100 if quick else 300)
    costs = Costs(edge_ms=round(edge_ms, 3))
    out: dict = {"calibration": cal, "edge_median_ms_measured": round(edge_ms, 3)}
    out["policies"] = {
        "edge only": serve(make_router(models, 0.0, costs), x, y),
        f"hybrid (threshold {th})": serve(make_router(models, th, costs), x, y),
        "cloud only": serve(make_router(models, 1.01, costs), x, y),
    }
    out["outage"] = {
        "with circuit breaker": outage_scenario(models, th, x, y, True),
        "without circuit breaker": outage_scenario(models, th, x, y, False),
    }
    out["drift"] = {}
    for shift in (0.5, 1.0):
        for controlled in (False, True):
            rows = drift_scenario(models, th, shift, controlled, windows=8 if quick else 12)
            post = [r for r in rows if r["drifted"]]
            out["drift"][
                f"{int(shift * 100)}% hard cases, {'controlled' if controlled else 'static threshold'}"
            ] = {
                "accuracy_after_onset": round(float(np.mean([r["accuracy"] for r in post])), 4),
                "cloud_share_after_onset": round(
                    float(np.mean([r["cloud_share"] for r in post])), 3
                ),
                "windows_to_first_alert": next(
                    (r["window"] - post[0]["window"] + 1 for r in post if r["alerting_features"]),
                    None,
                ),
                "false_alert_windows_before_onset": sum(
                    1 for r in rows if not r["drifted"] and r["alerting_features"]
                ),
            }
    return out


def render(res: dict) -> str:
    cal = res["calibration"]
    md = [
        "# Results (POC 099: REAL int8 ONNX edge model + REAL drift monitor/router; cloud latency and price MODELLED; no language model)",
        f"Edge accuracy {cal['edge_accuracy']}, cloud accuracy {cal['cloud_accuracy']} on 3,000 validation requests; "
        f"calibrated threshold (within 1 point of cloud-only): **{cal['threshold']}**. Measured edge median latency: {res['edge_median_ms_measured']} ms "
        "(noisy, not asserted). Cloud RTT 120 ms, timeout 400 ms and 0.0004 USD per call are ASSUMED.",
        "\n## Policies on a clean stream\n",
        markdown_table(
            [
                "policy",
                "accuracy",
                "cloud share",
                "mean latency ms (modelled)",
                "p95 ms",
                "USD per 1k",
            ],
            [
                [
                    k,
                    f"{v['accuracy']:.3f}",
                    f"{v['cloud_share']:.2f}",
                    f"{v['mean_latency_ms']:.2f}",
                    f"{v['p95_latency_ms']:.1f}",
                    f"{v['usd_per_1k']:.3f}",
                ]
                for k, v in res["policies"].items()
            ],
        ),
        "\n## Cloud outage (requests 300-699 of 1,500)\n",
        markdown_table(
            [
                "router",
                "accuracy",
                "cloud share",
                "degraded (edge fallback) share",
                "mean latency ms (modelled)",
                "p95 ms",
            ],
            [
                [
                    k,
                    f"{v['accuracy']:.3f}",
                    f"{v['cloud_share']:.2f}",
                    f"{v['degraded_share']:.2f}",
                    f"{v['mean_latency_ms']:.1f}",
                    f"{v['p95_latency_ms']:.1f}",
                ]
                for k, v in res["outage"].items()
            ],
        ),
        "\n## Traffic-mix drift towards hard cases (onset after window 4)\n",
        markdown_table(
            [
                "scenario",
                "accuracy after onset",
                "cloud share after onset",
                "windows to first alert",
                "false-alert windows before onset",
            ],
            [
                [
                    k,
                    v["accuracy_after_onset"],
                    v["cloud_share_after_onset"],
                    v["windows_to_first_alert"],
                    v["false_alert_windows_before_onset"],
                ]
                for k, v in res["drift"].items()
            ],
        ),
    ]
    return "\n".join(md) + "\n"


def main() -> None:
    import os

    res = run(quick=bool(os.environ.get("DEMO_QUICK")))
    out = POC_DIR / "results"
    out.mkdir(exist_ok=True)
    write_json(out / "metrics.json", res)
    (out / "results.md").write_text(render(res))
    print(render(res))


if __name__ == "__main__":
    main()
