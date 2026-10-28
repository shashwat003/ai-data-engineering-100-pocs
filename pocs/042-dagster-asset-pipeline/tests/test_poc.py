import tempfile
from pathlib import Path

import dagster as dg
import duckdb
import pytest
from dagster_asset_pipeline import source
from dagster_asset_pipeline.definitions import ASSETS, CHECKS, PARTS, make_defs
from dagster_asset_pipeline.main import counts, run, run_partition, run_summary

R = run(7)
FIRST = {r["day"]: r for r in R["first_pass"]}


def expected_day(day, outage=None):
    rows = source.events_for(day, outage)
    seen, clean = set(), []
    for r in rows:
        if r["user_id"] is None or r["amount"] < 0 or r["event_id"] in seen:
            continue
        seen.add(r["event_id"])
        clean.append(r)
    return len(rows), len(clean), round(sum(r["amount"] for r in clean), 2)


def test_blocking_check_stops_downstream_for_the_empty_partition_only():
    assert R["blocked_days"] == ["2025-03-05"]
    blocked = FIRST["2025-03-05"]
    assert blocked["materialized"] == ["raw_events"] and blocked["checks"] == {
        "raw_has_rows": False
    }
    for d, r in FIRST.items():
        if d != "2025-03-05":
            assert (
                r["success"]
                and r["materialized"] == ["clean_events", "daily_metrics", "raw_events"]
                and all(r["checks"].values())
            )


def test_summary_covers_only_healthy_days_until_the_source_is_fixed():
    assert R["summary_before_fix"]["days_with_metrics"] == 6
    assert R["counts_before_fix"]["daily_metrics"] == 6


def test_repairing_one_partition_updates_everything_downstream():
    assert R["fixed_run"]["success"] and R["summary_after_fix"]["days_with_metrics"] == 7
    assert R["summary_after_fix"]["revenue"] > R["summary_before_fix"]["revenue"]


def test_rerunning_all_partitions_is_idempotent():
    assert (
        R["rerun_all_ok"]
        and R["counts_after_rerun"] == R["counts_after_fix"]
        and R["summary_after_rerun"] == R["summary_after_fix"]
    )


def test_results_match_an_independent_python_computation():
    total_rev = 0.0
    n_raw = n_clean = 0
    for d in R["days"]:
        raw, clean, rev = expected_day(d, set())
        n_raw, n_clean, total_rev = n_raw + raw, n_clean + clean, total_rev + rev
    assert (
        R["counts_after_rerun"]["raw_events"] == n_raw
        and R["counts_after_rerun"]["clean_events"] == n_clean
    )
    assert R["summary_after_rerun"]["revenue"] == pytest.approx(total_rev, abs=0.05)


def test_quarantine_holds_every_removed_row_with_a_reason():
    with tempfile.TemporaryDirectory() as t:
        db = Path(t) / "w.duckdb"
        run_partition(db, "2025-03-02")
        con = duckdb.connect(str(db), read_only=True)
        reasons = dict(con.execute("select reason, count(*) from quarantine group by 1").fetchall())
        raw, clean = counts(db)["raw_events"], counts(db)["clean_events"]
        con.close()
    assert (
        set(reasons) == {"null_user", "negative_amount"}
        and raw - clean - sum(reasons.values()) == 5
    )  # exactly the 5 delivered duplicates


def test_non_blocking_check_warns_but_does_not_stop_the_run(monkeypatch):
    real = source.events_for

    def dirty(day, outage=None):
        rows = real(day, outage)
        for r in rows[: len(rows) // 3]:
            r["user_id"] = None
        return rows

    monkeypatch.setattr(source, "events_for", dirty)
    with tempfile.TemporaryDirectory() as t:
        res = run_partition(Path(t) / "w.duckdb", "2025-03-02")
    assert res["success"] and res["checks"]["quarantine_rate_is_low"] is False
    assert "daily_metrics" in res["materialized"]


def test_partitions_before_the_start_date_are_rejected():
    with tempfile.TemporaryDirectory() as t, pytest.raises(Exception, match="(?i)partition"):
        make_defs(Path(t) / "w.duckdb").resolve_implicit_global_asset_job_def().execute_in_process(
            partition_key="2024-01-01", asset_selection=[dg.AssetKey("raw_events")]
        )


def test_definitions_are_loadable_and_declare_blocking_checks():
    dg.Definitions.validate_loadable(make_defs("/tmp/_unused.duckdb"))
    assert len(ASSETS) == 4 and len(CHECKS) == 3 and PARTS.start.year == 2025
    blocking = {s.name for c in CHECKS for s in c.check_specs if s.blocking}
    assert blocking == {"raw_has_rows", "clean_has_no_bad_rows"}


def test_unpartitioned_summary_runs_on_existing_data(tmp_path):
    db = tmp_path / "w.duckdb"
    for d in ("2025-03-01", "2025-03-02"):
        run_partition(db, d)
    assert run_summary(db)["days_with_metrics"] == 2
