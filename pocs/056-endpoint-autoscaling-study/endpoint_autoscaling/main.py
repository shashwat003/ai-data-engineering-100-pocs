"""POC 056: autoscaling policies for an inference endpoint on a diurnal load with flash bursts, in a validated queueing simulation."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from pocs_common import write_json
from pocs_common.report import markdown_table

from .policies import Scheduled, StepScaling, TargetTracking, fixed
from .sim import Config, arrivals, erlang_c_wait, simulate

POC_DIR = Path(__file__).resolve().parent.parent
DAY = 86400.0
CFG = Config(
    workers_per_instance=4,
    service_mean_s=0.4,
    service_sigma=0.4,
    startup_s=180.0,
    queue_timeout_s=10.0,
    slo_s=1.0,
)
BURSTS = [
    (9.5 * 3600, 600),
    (15 * 3600, 600),
    (20.25 * 3600, 600),
]  # (start s, duration s): surprise 2.5x spikes


def rate(t: float) -> float:
    tod = t % DAY
    base = 3 + 22 * max(0.0, math.sin(math.pi * (tod - 6 * 3600) / (12 * 3600))) ** 2
    burst = 2.5 if any(a <= t < a + d for a, d in BURSTS) else 1.0
    return base * burst


def validate_against_erlang_c(seed: int = 0) -> dict:
    """Simulated mean queueing delay vs the Erlang C formula for a fixed M/M/c queue (no autoscaling)."""
    cfg = Config(
        workers_per_instance=4,
        service_mean_s=0.4,
        exponential_service=True,
        queue_timeout_s=1e9,
        seed=seed,
    )
    out = {}
    for lam in (4.0, 7.0, 8.5):
        arr = arrivals(lambda t, lam=lam: lam, 20_000, seed)
        res = simulate(cfg, arr, fixed(1), 20_000, initial_instances=1)
        sim_wait = float(np.mean(res.latencies) - cfg.service_mean_s)
        theory = erlang_c_wait(lam, 1 / cfg.service_mean_s, 4)
        out[f"lambda={lam} rps (rho={lam * cfg.service_mean_s / 4:.2f})"] = {
            "simulated_mean_wait_s": round(sim_wait, 4),
            "erlang_c_mean_wait_s": round(theory, 4),
        }
    return out


def policies() -> dict:
    floors = [(7.5 * 3600, 18.5 * 3600, 5)]
    return {
        "fixed 2 instances": fixed(2),
        "fixed 6 (peak-sized)": fixed(6),
        "fixed 12 (burst-proof)": fixed(12),
        "target tracking (6 rps/instance)": TargetTracking(6.0),
        "step scaling (util 30-70%)": StepScaling(),
        "scheduled floor + target tracking": Scheduled(floors, TargetTracking(6.0)),
    }


def run(horizon: float = DAY, seed: int = 1) -> dict:
    arr = arrivals(rate, horizon, seed)
    res = {
        "requests": int(len(arr)),
        "erlang_c_validation": validate_against_erlang_c(),
        "policies": {},
    }
    for name, pol in policies().items():
        init = int(name.split()[1]) if name.startswith("fixed") else 2
        r = simulate(CFG, arr, pol, horizon, initial_instances=init)
        res["policies"][name] = r.summary(CFG)
    return res


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    rows = [
        [
            k,
            v["p95_ms"],
            v["p99_ms"],
            v["slo_violation_pct"],
            v["dropped_pct"],
            v["instance_hours"],
            v["cost_usd"],
            v["scale_events"],
        ]
        for k, v in r["policies"].items()
    ]
    md = ["# Results (measured by `python -m endpoint_autoscaling.main`; discrete-event SIMULATION validated against Erlang C; no SageMaker, no cloud; price assumed)", "",
          f"{r['requests']:,} requests over 24 h: diurnal load (3 to 25 rps) plus three surprise 10-minute bursts at 2.5x. Instance = 4 workers, mean service 0.4 s (capacity 10 rps), 180 s to start an instance, SLO 1 s, queue timeout 10 s.", "",
          "## Simulator vs queueing theory (M/M/4, mean wait)", "", markdown_table(["load", "simulated (s)", "Erlang C (s)"], [[k, v["simulated_mean_wait_s"], v["erlang_c_mean_wait_s"]] for k, v in r["erlang_c_validation"].items()]), "", "## Policies", "",
          markdown_table(["policy", "p95 ms", "p99 ms", "SLO violations %", "dropped %", "instance-hours", "cost USD (assumed $0.23/h)", "scale events"], rows)]  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
