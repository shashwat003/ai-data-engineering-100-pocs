"""POC 057: measure Spark tuning knobs locally, price each configuration with an EMR Serverless cost model, check the application plan."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .cost import Worker, check_plan, idle_cost_per_month, job_cost
from .experiments import run_all

POC_DIR = Path(__file__).resolve().parent.parent
INFRA = POC_DIR / "infra"
SCALE = (
    200  # MODELLED: project the measured runtimes to a dataset 200x larger, assuming linear scaling
)


def terraform_plan(extra: list[str] | None = None) -> dict | None:
    if shutil.which("terraform") is None:
        return None
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
    cache = Path(tempfile.gettempdir()) / "tf-plugin-cache"
    cache.mkdir(exist_ok=True)
    env.update(
        {
            "TF_IN_AUTOMATION": "1",
            "TF_INPUT": "0",
            "TF_PLUGIN_CACHE_DIR": str(cache),
            "AWS_ACCESS_KEY_ID": "mock",
            "AWS_SECRET_ACCESS_KEY": "mock",
            "AWS_EC2_METADATA_DISABLED": "true",
        }
    )
    with tempfile.TemporaryDirectory() as t:
        d = Path(t) / "infra"
        shutil.copytree(
            INFRA, d, ignore=shutil.ignore_patterns(".terraform*", "*.tfstate*", "*.tfplan")
        )
        for args in (
            ["init", "-backend=false", "-no-color"],
            ["plan", "-no-color", "-lock=false", "-out=p.tfplan", *(extra or [])],
        ):
            p = subprocess.run(
                ["terraform", *args],
                cwd=d,
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            if p.returncode:
                raise RuntimeError(
                    f"terraform {args[0]} failed:\n{p.stdout[-800:]}\n{p.stderr[-800:]}"
                )
        return json.loads(
            subprocess.run(
                ["terraform", "show", "-json", "p.tfplan"],
                cwd=d,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )


def price(measured: dict) -> dict:
    """Attach modelled costs: the measured small run, and the same configuration projected to a SCALE-times larger dataset."""
    out = []
    for e in measured["experiments"]:
        w = (
            e["workers_vcpu"] // 4 or 1
        )  # workers of 4 vCPU; local[1], local[2] are billed as one worker
        small = job_cost(e["runtime_s"], w)
        big = job_cost(e["runtime_s"] * SCALE, w)
        out.append(
            {
                **e,
                "workers_billed": w,
                "cost_small_usd": small["cost_usd"],
                "billed_s_small": small["billed_s"],
                "projected_runtime_s": round(e["runtime_s"] * SCALE, 1),
                "projected_cost_usd": big["cost_usd"],
            }
        )
    return {**measured, "experiments": out}


def run(quick: bool = False, with_terraform: bool = True) -> dict:
    measured = run_all(n_orders=300_000 if quick else 1_500_000, repeats=1 if quick else 3)
    res = price(measured)
    res["scale_factor_modelled"] = SCALE
    res["idle_cost_per_month_usd"] = {
        f"{n} pre-initialised 4 vCPU workers": idle_cost_per_month(n, Worker()) for n in (1, 2, 5)
    }
    if with_terraform and shutil.which("terraform"):
        variants = {
            "default": [],
            "no cost ceiling (max 512 vCPU)": ["-var", "max_vcpu=512"],
            "x86 workers": ["-var", "architecture=X86_64"],
            "long idle timeout": ["-var", "idle_timeout_minutes=60"],
            "5 warm executors": ["-var", "preinit_executors=5"],
        }
        res["plan_checks"] = {k: check_plan(terraform_plan(v)) for k, v in variants.items()}
    else:
        res["plan_checks"] = {
            "status": "NOT RUN (terraform not available)" if with_terraform else "SKIPPED"
        }
    return res


def main() -> None:
    r = run(quick=bool(os.environ.get("DEMO_QUICK")))
    write_json(POC_DIR / "results" / "metrics.json", r)
    rows = [
        [
            e["group"],
            e["config"],
            e["runtime_s"],
            e["tasks"],
            e["cost_small_usd"],
            e["projected_runtime_s"],
            e["projected_cost_usd"],
        ]
        for e in r["experiments"]
    ]
    md = ["# Results (measured by `python -m emr_serverless_cost_tuning.main`; REAL local Spark timings, MODELLED EMR Serverless prices and 200x projection; terraform plan offline)", "",
          f"{r['n_orders']:,} synthetic orders; job = broadcast join + group-by + window top-3. All configurations returned identical results: **{r['all_configs_same_result']}**. Local timings are noisy (shared machine); read orders of magnitude, not decimals.", "",
          markdown_table(["experiment", "configuration", "runtime s (measured)", "tasks", "cost USD (this run, modelled)", f"runtime s projected x{r['scale_factor_modelled']}", "projected cost USD (modelled)"], rows), "",
          "Cost model: assumed list prices ($0.052624/vCPU-h, $0.0057785/GB-h, ARM64 20% cheaper), 4 vCPU/16 GB workers, 60 s minimum billing per worker, 15 s start-up. The measured jobs run for seconds, so the 60 s minimum dominates their modelled cost.", "",
          "## Idle cost of pre-initialised capacity (modelled, per month)", ""] + [f"- {k}: ${v}" for k, v in r["idle_cost_per_month_usd"].items()]  # fmt: skip
    md += ["", "## Terraform plan checks (offline)", ""]
    pc = r["plan_checks"]
    if "status" in pc:
        md.append(f"- {pc['status']}")
    else:
        md += [
            f"- **{k}**: {len(v)} finding(s){': ' + '; '.join(v) if v else ''}"
            for k, v in pc.items()
        ]
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
