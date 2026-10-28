"""POC 033: review dbt PR scenarios with the agent, then compare with what real dbt does."""

from __future__ import annotations

import tempfile
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .dbt_runner import BASE, dbt_build, ground_truth, make_head
from .project import load
from .review import comment_payload, review, risk_flag
from .scenarios import SCENARIOS

POC_DIR = Path(__file__).resolve().parent.parent


def agent_review(name: str):
    with tempfile.TemporaryDirectory() as t:
        head = load(make_head(name, Path(t) / "proj"))
    return review(load(BASE), head)


def run() -> dict:
    with tempfile.TemporaryDirectory() as t:
        base_res = dbt_build(BASE, Path(t))
    rows = []
    for name in SCENARIOS:
        findings = agent_review(name)
        gt = ground_truth(name, base_res["fingerprint"])
        rows.append({"scenario": name, "findings": [(f.severity, f.category, f.code, f.model) for f in findings], "agent_flags_risk": risk_flag(findings), "dbt_ok": gt["dbt_ok"],
                     "data_changed_models": gt["data_changed_models"], "truth_risky": gt["risky"], "verdict": comment_payload(findings)["event"]})  # fmt: skip
    tp = sum(r["agent_flags_risk"] and r["truth_risky"] for r in rows)
    fp = sum(r["agent_flags_risk"] and not r["truth_risky"] for r in rows)
    fn = sum(not r["agent_flags_risk"] and r["truth_risky"] for r in rows)
    tn = sum(not r["agent_flags_risk"] and not r["truth_risky"] for r in rows)
    return {
        "base_build_ok": base_res["ok"],
        "scenarios": rows,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
    }


def main() -> None:
    res = run()
    write_json(POC_DIR / "results" / "results.json", res)
    table = [
        (
            r["scenario"],
            r["verdict"],
            ", ".join(sorted({f"{s}:{c}" for s, _cat, c, _m in r["findings"]})) or "-",
            "yes" if r["agent_flags_risk"] else "no",
            "FAIL" if not r["dbt_ok"] else "ok",
            ", ".join(r["data_changed_models"]) or "-",
        )
        for r in res["scenarios"]
    ]
    c = res["confusion"]
    md = (
        "# Results: agent review of 10 dbt PR scenarios vs what real dbt (dbt-core + dbt-duckdb) does\n\n"
        "The reviewer is deterministic (sqlglot + project metadata); no LLM wrote or judged anything. 'Risk flagged' = a finding in the risk category at warn or above; "
        "ground truth = dbt build fails or any pre-existing model's rows change.\n\n"
        + markdown_table(
            [
                "scenario",
                "verdict",
                "findings",
                "risk flagged",
                "dbt build",
                "models whose data changed",
            ],
            table,
        )
        + f"\n\nConfusion matrix over {len(res['scenarios'])} scenarios: TP {c['tp']}, FP {c['fp']}, FN {c['fn']}, TN {c['tn']}. Ten hand-made scenarios are a smoke test of the detectors, not a benchmark.\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
