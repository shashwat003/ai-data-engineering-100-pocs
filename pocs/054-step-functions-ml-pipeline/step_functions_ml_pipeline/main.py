"""POC 054: run an ML pipeline state machine through an ASL interpreter; Terraform-apply the same definition to a local mock."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pocs_common import write_json

from .asl import Machine, validate
from .pipeline import DEFINITION, Tasks

POC_DIR = Path(__file__).resolve().parent.parent
INPUT = {"seed": 3, "n_rows": 1500, "label_noise": 0.05, "grid": [0.01, 0.1, 1.0, 10.0]}


def scenario(name: str, payload: dict, **tasks_kw) -> dict:
    with tempfile.TemporaryDirectory() as t:
        tasks = Tasks(Path(t) / "registry", **tasks_kw)
        ex = Machine(DEFINITION, tasks.resources()).start(payload)
        states = [h[0] for h in ex.history if h[1] == "Entered"]
        retries = [h[1] for h in ex.history if h[1].startswith("Retry")]
        reg = Path(t, "registry", "latest.json")
        return {
            "scenario": name,
            "status": ex.status,
            "error": ex.error,
            "path": " > ".join(dict.fromkeys(states)),
            "retries": retries,
            "slept_seconds": ex.slept,
            "registered": json.loads(reg.read_text()) if reg.exists() else None,
            "notifications": len(tasks.notifications),
        }


def run() -> dict:
    res = {
        "validation_errors": validate(
            DEFINITION, {"prepare", "train", "select_best", "register", "notify"}
        )
    }
    res["scenarios"] = [
        scenario("happy path", INPUT),
        scenario(
            "transient storage errors (2 failures) are retried with backoff",
            INPUT,
            storage_failures=2,
        ),
        scenario("storage keeps failing -> Catch -> notify", INPUT, storage_failures=10),
        scenario(
            "one candidate times out once -> Map item retried", INPUT, timeouts_for_c={0.1: 1}
        ),
        scenario("too few positives -> quality gate fails", {**INPUT, "n_rows": 150}),
        scenario(
            "weak model (noisy labels) -> evaluation gate -> notify", {**INPUT, "label_noise": 0.45}
        ),
    ]
    res["mock_stack"] = _mock_stack()
    return res


def _mock_stack() -> dict:
    from aws_mock_harness.server import MockAws

    from .mock_stack import deploy_and_inspect

    with MockAws() as aws:
        return {"status": "deployed to local moto (boto3), not real AWS", **deploy_and_inspect(aws)}


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    md = [
        "# Results (measured by `python -m step_functions_ml_pipeline.main`; own ASL interpreter running real scikit-learn tasks)",
        "",
        f"- definition validation errors: {r['validation_errors'] or 'none'}",
        "",
    ]
    for s in r["scenarios"]:
        md.append(
            f"- **{s['scenario']}**: {s['status']}{' (' + s['error'] + ')' if s['error'] else ''}; path: `{s['path']}`; retries: {len(s['retries'])} (slept {s['slept_seconds']:g}s, simulated); registered: {s['registered']}; notifications: {s['notifications']}"
        )
    md.append(f"- **Deploy to local moto**: {r['mock_stack']}")
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
