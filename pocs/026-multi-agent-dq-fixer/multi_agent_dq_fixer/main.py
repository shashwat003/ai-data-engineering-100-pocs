"""POC 026: multi-agent data-quality triage with verified, gated fixes (rule-based agents; no LLM)."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .agents import run_pipeline
from .data import make

POC_DIR = Path(__file__).resolve().parent.parent
CHECK_TO_TRUTH = {("not_null", "customer_id"): "null_customer_id", ("in_set", "status"): "status_typo", ("range_min", "amount"): "negative_amount", ("not_future", "order_date"): "future_date", ("fk_exists", "customer_id"): "orphan_customer", ("unique", "order_id"): "duplicate_order_id"}  # fmt: skip


def run(n: int = 2000, seed: int = 6) -> dict:
    d = make(n, seed)
    s = run_pipeline(d["orders"], d["customers"])
    found = {CHECK_TO_TRUTH[(i.check, i.column)]: set(i.rows) for i in s.issues}
    detect = {
        k: {
            "injected": len(v),
            "found": len(found.get(k, set()) & set(v)),
            "extra": len(found.get(k, set()) - set(v)),
        }
        for k, v in d["truth"].items()
        if k != "hostile_note"
    }
    clean = d["clean"].set_index("order_id")
    out = s.clean.drop_duplicates("order_id").set_index("order_id")
    common = out.index.intersection(clean.index)
    changed_vs_truth = (out.loc[common, "status"] != clean.loc[common, "status"]).sum()
    fixed_status = d["truth"]["status_typo"]
    status_ok = int((out.loc[fixed_status, "status"] == clean.loc[fixed_status, "status"]).sum())
    s2 = run_pipeline(s.clean, d["customers"])
    return {"rows_in": len(d["orders"]), "rows_out": len(s.clean), "detect": detect, "auto_applied": [(r["op"], r["issue"], r["rows"]) for r in s.applied],
            "queued": [(r["op"], r["issue"], r["rows"], r["decision"]) for r in s.queued], "status_fixed_correctly": status_ok, "status_typos": len(fixed_status),
            "status_still_wrong_vs_truth": int(changed_vs_truth), "remaining_issues_after_auto": len(s2.issues), "second_run_auto_applied": len(s2.applied),
            "triage": s.triage, "trace_steps": len(s.trace)}  # fmt: skip


def main() -> None:
    res = run()
    write_json(POC_DIR / "results" / "results.json", res)
    md = (
        "# Results: multi-agent data-quality triage on synthetic orders (rule-based agents, no LLM)\n\n"
        + markdown_table(
            ["defect", "injected", "found", "extra flags"],
            [(k, v["injected"], v["found"], v["extra"]) for k, v in res["detect"].items()],
        )
        + "\n\n"
        + markdown_table(
            ["issue", "severity", "category", "owner", "rows"],
            [
                (f"{t['check']}:{t['column']}", t["severity"], t["category"], t["owner"], t["n"])
                for t in res["triage"]
            ],
        )
        + f"\n\nAuto-applied (verified, confidence >= 0.9, <= 5% rows): {res['auto_applied']}. Queued for a human: {len(res['queued'])} proposals "
        f"(null/orphan customers, negative amounts, future dates are quarantine proposals, never guessed). Status typos repaired correctly: "
        f"{res['status_fixed_correctly']}/{res['status_typos']}. Remaining issues after the auto fixes: {res['remaining_issues_after_auto']}; "
        f"a second run auto-applies {res['second_run_auto_applied']} further fixes (idempotent).\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
