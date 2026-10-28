"""POC 064: OIDC trust and workflow linting, Terraform plan policy checks, and blue/green deployment simulation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .bluegreen import World, deploy
from .oidc import lint_permissions, lint_trust_policy
from .plan_checks import check_plan
from .workflow_lint import lint_workflow

POC_DIR = Path(__file__).resolve().parent.parent
FIX = POC_DIR / "fixtures"
INFRA = POC_DIR / "infra"


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


def corpus_results() -> dict:
    out = {"trust": [], "permissions": [], "workflows": []}
    for f in sorted((FIX / "trust").glob("*.json")):
        doc = json.loads(f.read_text())
        out["trust"].append([f.stem, len(lint_trust_policy(doc, account_id="123456789012"))])
    for f in sorted((FIX / "permissions").glob("*.json")):
        out["permissions"].append([f.stem, len(lint_permissions(json.loads(f.read_text())))])
    for f in sorted((FIX / "workflows").glob("*.yml")):
        out["workflows"].append([f.stem, len(lint_workflow(f.read_text()))])
    return out


SCENARIOS = {
    "healthy release": dict(),
    "green tasks never start (breaker)": dict(green_start_fail_rate=1.0),
    "flaky starts (30%), recovers": dict(green_start_fail_rate=0.3),
    "green returns 5xx (5%)": dict(green_error_rate=0.05),
    "green mildly worse (0.8pp), inside tolerance": dict(green_error_rate=0.010),
    "controller crash mid-rollout": dict(_crash=8),
}


def simulate(seeds: int = 20) -> dict:
    res = {}
    for name, cfg in SCENARIOS.items():
        crash = cfg.get("_crash")
        outs = [
            deploy(
                World(seed=s, **{k: v for k, v in cfg.items() if not k.startswith("_")}),
                crash_at=crash,
            )
            for s in range(seeds)
        ]
        res[name] = {
            "completed": sum(o["result"] == "completed" for o in outs),
            "rolled_back": sum(o["result"] == "rolled_back" for o in outs),
            "mean_bad_requests_to_green": round(
                sum(o["bad_requests_served_by_green"] for o in outs) / seeds, 1
            ),
            "seeds": seeds,
        }
    return res


def run(with_terraform: bool = True) -> dict:
    res = {"corpus": corpus_results(), "simulation": simulate()}
    plan = terraform_plan() if with_terraform else None
    if plan is None:
        res["terraform"] = {"status": "NOT RUN" if with_terraform else "SKIPPED"}
        return res
    variants = {
        "clean": [],
        "unpinned image": [
            "-var",
            "image=123456789012.dkr.ecr.eu-west-1.amazonaws.com/inference-api:latest",
        ],
        "trust any repo of the owner": ["-var", "deploy_subject=repo:example-org/*"],
        "trust has no environment": ["-var", "deploy_subject=repo:example-org/inference-api:*"],
    }
    res["terraform"] = {
        "status": "planned offline (never applied, no AWS contact)",
        "resources_in_plan": len(plan["resource_changes"]),
        "variants": {
            k: check_plan(plan if k == "clean" else terraform_plan(v)) for k, v in variants.items()
        },
    }
    return res


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    md = [
        "# Results (measured by `python -m fargate_bluegreen_api.main`; offline Terraform plan, no AWS; deployment SIMULATED)",
        "",
        "## Linter corpus (findings per fixture; `good_*` must be 0, everything else must be > 0)",
        "",
    ]
    for k, rows in r["corpus"].items():
        md += [f"**{k}**", "", markdown_table(["fixture", "findings"], rows), ""]
    md += [
        "## Blue/green simulation (20 seeds each)",
        "",
        markdown_table(
            ["scenario", "completed", "rolled back", "mean bad requests served by green"],
            [
                [k, v["completed"], v["rolled_back"], v["mean_bad_requests_to_green"]]
                for k, v in r["simulation"].items()
            ],
        ),
        "",
    ]
    t = r["terraform"]
    md += ["## Terraform plan policy checks", "", f"- status: {t['status']}"]
    for k, f in t.get("variants", {}).items():
        md.append(f"- **{k}**: {len(f)} finding(s){': ' + '; '.join(f) if f else ''}")
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
