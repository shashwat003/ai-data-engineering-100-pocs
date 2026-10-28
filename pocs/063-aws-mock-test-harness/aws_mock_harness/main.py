"""POC 063: measure the harness (startup, reset cost, isolation) and probe what the local mock really supports."""

from __future__ import annotations

import time
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from . import helpers
from .guard import RealAwsBlocked, forbid_real_aws
from .probe import run_probes
from .server import MockAws

POC_DIR = Path(__file__).resolve().parent.parent


def run(n_cycles: int = 50) -> dict:
    out: dict = {}
    with MockAws() as aws:
        out["startup_seconds"] = round(aws.startup_seconds, 2)
        t0 = time.perf_counter()
        for i in range(n_cycles):  # simulated test lifecycle: reset, create, use
            aws.reset()
            helpers.make_bucket(aws, f"bucket-{i}")
            helpers.put_objects(aws, f"bucket-{i}", {"k": "v"})
        out["ms_per_test_cycle"] = round((time.perf_counter() - t0) / n_cycles * 1000, 1)
        aws.reset()
        helpers.make_bucket(aws, "leak-check")
        aws.reset()
        out["state_cleared_by_reset"] = aws.client("s3").list_buckets()["Buckets"] == []
        import boto3

        with forbid_real_aws():
            try:
                boto3.client(
                    "s3", region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="y"
                ).list_buckets()
                out["real_aws_blocked"] = False
            except RealAwsBlocked:
                out["real_aws_blocked"] = True
        out["probes"] = run_probes(aws)
    return out


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    rows = [[k, "yes" if v["supported"] else "NO", v["detail"]] for k, v in r["probes"].items()]
    md = ["# Results (measured by `python -m aws_mock_harness.main`; moto server, no Docker, no LocalStack)", "",
          f"- server startup: {r['startup_seconds']} s; reset+create+put per simulated test: {r['ms_per_test_cycle']} ms", f"- state cleared by reset: {r['state_cleared_by_reset']}; request to real AWS blocked by guard: {r['real_aws_blocked']}", "",
          "## What the mock supports (one round-trip probe per service)", "", markdown_table(["probe", "works", "detail"], rows)]  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
