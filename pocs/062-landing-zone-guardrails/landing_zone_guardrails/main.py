"""POC 062: lint + simulate SCPs, apply them with Terraform to a local mock organisation, read back, detect drift."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .lint import lint_org
from .matrix import mutation_report, run_matrix
from .simulator import Organisation
from .tooling import (
    POC,
    moto_server,
    org_client,
    read_back,
    reconcile_defaults,
    terraform_available,
    tf,
    workspace,
)

OPTS = ["-no-color", "-lock=false"]


def expected_attachments(cfg: dict) -> dict[str, list[str]]:
    exp = {"root": sorted(cfg["root"]["policies"])}
    exp.update({ou: sorted(o["policies"]) for ou, o in cfg["root"]["ous"].items()})
    return exp


def terraform_section() -> dict:
    if not terraform_available():
        return {"status": "NOT RUN", "reason": "terraform binary not found"}
    cfg = json.loads((POC / "org.json").read_text())
    out: dict = {"status": "RAN against local moto, not real AWS"}
    with moto_server() as port, tempfile.TemporaryDirectory() as t:
        org_client(port).create_organization(FeatureSet="ALL")
        infra = workspace(Path(t))
        tf(["init", "-backend=false", "-no-color"], infra)
        v = ["-var", f"mock_port={port}"]
        tf(["apply", "-auto-approve", *OPTS, *v], infra)
        rb = read_back(port)
        out["policies_created"] = sorted(n for n in rb["policies"] if n != "FullAWSAccess")
        out["content_matches_files"] = all(
            rb["policies"][n]["content"] == json.loads((POC / "policies" / f"{n}.json").read_text())
            for n in rb["policies"]
            if (POC / "policies" / f"{n}.json").exists()
        )
        want = expected_attachments(cfg)
        org = Organisation.load(POC / "org.json", POC / "policies")
        out["attachments_match_org_json_after_apply"] = all(
            sorted(rb["attachments"][k]) == v_ for k, v_ in want.items()
        )
        actual = org.with_attachments(rb["attachments"])
        out["matrix_on_actual_state_after_apply"] = (
            f"{sum(r['ok'] for r in run_matrix(actual))}/{len(run_matrix(actual))}"
        )
        out["allowlist_gap_cases_after_apply"] = [
            r["id"] for r in run_matrix(actual) if not r["ok"]
        ]
        out["reconciled_ous"] = reconcile_defaults(port, cfg)
        rb = read_back(port)
        out["attachments_match_org_json_after_reconcile"] = all(
            sorted(rb["attachments"][k]) == v_ for k, v_ in want.items()
        )
        actual = org.with_attachments(rb["attachments"])
        out["matrix_on_actual_state_after_reconcile"] = (
            f"{sum(r['ok'] for r in run_matrix(actual))}/{len(run_matrix(actual))}"
        )
        out["clean_replan_exit_code"] = tf(
            ["plan", "-detailed-exitcode", *OPTS, *v], infra, check=False
        ).returncode
        c = org_client(port)
        pid = rb["policies"]["region-lock"]["id"]
        weak = json.loads(json.dumps(rb["policies"]["region-lock"]["content"]))
        weak["Statement"][0]["Condition"]["StringNotEquals"]["aws:RequestedRegion"].append(
            "us-east-1"
        )
        c.update_policy(PolicyId=pid, Content=json.dumps(weak))
        out["drift_plan_exit_code_after_console_edit"] = tf(
            ["plan", "-detailed-exitcode", *OPTS, *v], infra, check=False
        ).returncode
        tf(["apply", "-auto-approve", *OPTS, *v], infra)
        out["plan_exit_code_after_reapply"] = tf(
            ["plan", "-detailed-exitcode", *OPTS, *v], infra, check=False
        ).returncode
        cfg_rules = json.loads(tf(["show", "-json", "-no-color"], infra).stdout)["values"][
            "root_module"
        ]["resources"]
        out["config_rules_declared"] = sorted(
            r["values"]["source"][0]["source_identifier"]
            for r in cfg_rules
            if r["type"] == "aws_config_config_rule"
        )
    out["required_config_rules_present"] = out["config_rules_declared"] == sorted(
        cfg["required_config_rules"]
    )
    return out


def run(with_terraform: bool = True) -> dict:
    org = Organisation.load(POC / "org.json", POC / "policies")
    cfg = json.loads((POC / "org.json").read_text())
    matrix = run_matrix(org)
    res = {
        "lint_problems": lint_org(org, cfg["break_glass_role"]),
        "matrix": {
            "cases": len(matrix),
            "passed": sum(r["ok"] for r in matrix),
            "failures": [r for r in matrix if not r["ok"]],
        },
        "mutation": mutation_report(org),
        "sample_decisions": {r["id"]: f"{r['got']}: {r['reason']}" for r in matrix[:8]},
    }
    res["terraform"] = terraform_section() if with_terraform else {"status": "SKIPPED"}
    return res


def main() -> None:
    r = run()
    write_json(POC / "results" / "metrics.json", r)
    m = r["matrix"]
    md = ["# Results (measured by `python -m landing_zone_guardrails.main`; SCP semantics SIMULATED, Terraform applied to a LOCAL moto organisation)", "",
          f"- lint problems: {len(r['lint_problems'])} {r['lint_problems']}", f"- regression matrix: {m['passed']}/{m['cases']} expectations hold", "", "## Which cases each guardrail protects (cases that flip when the policy is removed)", "",
          markdown_table(["policy", "cases that flip", "n"], [[p, ", ".join(v) or "NONE (untested!)", len(v)] for p, v in r["mutation"].items()]), "", "## Terraform against local moto", ""]  # fmt: skip
    md += [f"- {k}: {v}" for k, v in r["terraform"].items()]
    (POC / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
