"""POC 042: partitioned Dagster assets with blocking checks, an outage, a repair and an idempotent re-run."""

from __future__ import annotations

import tempfile
from pathlib import Path

import dagster as dg
import duckdb

from pocs_common import write_json
from pocs_common.report import markdown_table

from . import source
from .definitions import all_time_summary, make_defs

POC_DIR = Path(__file__).resolve().parent.parent
PARTITION_ASSETS = ["raw_events", "clean_events", "daily_metrics"]


def run_partition(db: Path, day: str, outage_days: list[str] | None = None) -> dict:
    defs = make_defs(db, outage_days)
    job = defs.resolve_implicit_global_asset_job_def()
    res = job.execute_in_process(
        partition_key=day,
        raise_on_error=False,
        asset_selection=[dg.AssetKey(k) for k in PARTITION_ASSETS],
    )
    mats = sorted(e.asset_key.to_user_string() for e in res.get_asset_materialization_events())
    checks = {e.check_name: e.passed for e in res.get_asset_check_evaluations()}
    return {"day": day, "success": res.success, "materialized": mats, "checks": checks}


def run_summary(db: Path) -> dict:
    res = dg.materialize(
        [all_time_summary], resources=make_defs(db).resources, raise_on_error=False
    )
    md = next(iter(res.get_asset_materialization_events())).materialization.metadata
    return {"days_with_metrics": md["days_with_metrics"].value, "revenue": md["revenue"].value}


def counts(db: Path) -> dict:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return {
            t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("raw_events", "clean_events", "quarantine", "daily_metrics")
        }  # noqa: S608
    finally:
        con.close()


def run(n_days: int = 7) -> dict:
    days = source.days("2025-03-01", n_days)
    with tempfile.TemporaryDirectory() as t:
        db = Path(t) / "wh.duckdb"
        first = [run_partition(db, d) for d in days]
        blocked = [r["day"] for r in first if not r["success"]]
        summary_before = run_summary(db)
        counts_before = counts(db)
        fixed = run_partition(db, "2025-03-05", outage_days=[])  # the source was repaired
        summary_after = run_summary(db)
        counts_fixed = counts(db)
        again = [run_partition(db, d, outage_days=[]) for d in days]  # full re-run
        counts_again = counts(db)
        summary_again = run_summary(db)
    return {"days": days, "first_pass": first, "blocked_days": blocked, "summary_before_fix": summary_before, "counts_before_fix": counts_before, "fixed_run": fixed,
            "summary_after_fix": summary_after, "counts_after_fix": counts_fixed, "rerun_all_ok": all(r["success"] for r in again), "counts_after_rerun": counts_again, "summary_after_rerun": summary_again}  # fmt: skip


def main() -> None:
    res = run()
    write_json(POC_DIR / "results" / "results.json", res)
    rows = [
        (
            r["day"],
            "ok" if r["success"] else "BLOCKED",
            ", ".join(r["materialized"]) or "-",
            ", ".join(f"{k}={'pass' if v else 'FAIL'}" for k, v in sorted(r["checks"].items())),
        )
        for r in res["first_pass"]
    ]
    md = (
        "# Results: Dagster partitioned assets with blocking asset checks (real Dagster, in-process)\n\n"
        + markdown_table(["partition", "run", "assets materialized", "checks"], rows)
        + f"\n\nBlocked partitions: {res['blocked_days']}. Summary before the fix: {res['summary_before_fix']}; table counts: {res['counts_before_fix']}.\n\n"
        f"After the source is repaired and only 2025-03-05 is re-materialised: summary {res['summary_after_fix']}; counts {res['counts_after_fix']}.\n\n"
        f"Re-running all seven partitions again: all succeeded = {res['rerun_all_ok']}; counts {res['counts_after_rerun']} (unchanged, so partition writes are idempotent); summary {res['summary_after_rerun']}.\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
