"""Dagster assets: partitioned raw -> clean -> metrics, blocking asset checks, idempotent partition writes."""

import threading
from pathlib import Path

import dagster as dg
import duckdb
from dagster import AssetCheckExecutionContext, AssetExecutionContext

from . import source

PARTS = dg.DailyPartitionsDefinition(start_date="2025-03-01", end_offset=0)


class Warehouse(dg.ConfigurableResource):
    path: str
    outage_days: list[str] = sorted(
        source.OUTAGE_DAYS
    )  # operators can clear this to simulate the source being fixed

    def connect(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect(self.path)
        con.execute(
            "CREATE TABLE IF NOT EXISTS raw_events (event_id VARCHAR, user_id VARCHAR, amount DOUBLE, event_date VARCHAR)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS clean_events (event_id VARCHAR, user_id VARCHAR, amount DOUBLE, event_date VARCHAR)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS quarantine (event_id VARCHAR, reason VARCHAR, event_date VARCHAR)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS daily_metrics (event_date VARCHAR, revenue DOUBLE, events BIGINT, users BIGINT)"
        )
        return con


_LOCK = threading.Lock()  # one writer at a time against the single DuckDB file


@dg.asset(partitions_def=PARTS, group_name="ingest")
def raw_events(context: AssetExecutionContext, wh: Warehouse) -> dg.MaterializeResult:
    day = context.partition_key
    rows = source.events_for(day, set(wh.outage_days))
    with _LOCK:
        con = wh.connect()
        con.execute(
            "DELETE FROM raw_events WHERE event_date = ?", [day]
        )  # partition overwrite = idempotent
        if rows:  # an empty day is a data problem for the check to report, not a crash
            con.executemany(
                "INSERT INTO raw_events VALUES (?,?,?,?)",
                [(r["event_id"], r["user_id"], r["amount"], r["event_date"]) for r in rows],
            )
        con.close()
    return dg.MaterializeResult(metadata={"rows": len(rows)})


@dg.asset_check(asset=raw_events, blocking=True)
def raw_has_rows(context: AssetCheckExecutionContext, wh: Warehouse) -> dg.AssetCheckResult:
    day = context.partition_key
    with _LOCK:
        con = wh.connect()
        n = con.execute("SELECT count(*) FROM raw_events WHERE event_date = ?", [day]).fetchone()[0]
        con.close()
    return dg.AssetCheckResult(
        passed=n > 0, metadata={"rows": n}, severity=dg.AssetCheckSeverity.ERROR
    )


@dg.asset(partitions_def=PARTS, deps=[raw_events], group_name="transform", check_specs=[])
def clean_events(context: AssetExecutionContext, wh: Warehouse) -> dg.MaterializeResult:
    day = context.partition_key
    with _LOCK:
        con = wh.connect()
        con.execute("DELETE FROM clean_events WHERE event_date = ?", [day])
        con.execute("DELETE FROM quarantine WHERE event_date = ?", [day])
        con.execute(
            "INSERT INTO quarantine SELECT event_id, 'null_user', event_date FROM raw_events WHERE event_date = ? AND user_id IS NULL",
            [day],
        )
        con.execute(
            "INSERT INTO quarantine SELECT event_id, 'negative_amount', event_date FROM raw_events WHERE event_date = ? AND user_id IS NOT NULL AND amount < 0",
            [day],
        )
        con.execute(
            "INSERT INTO clean_events SELECT DISTINCT * FROM raw_events WHERE event_date = ? AND user_id IS NOT NULL AND amount >= 0",
            [day],
        )
        total, clean, quarantined = (con.execute(q, [day]).fetchone()[0] for q in (
            "SELECT count(*) FROM raw_events WHERE event_date = ?", "SELECT count(*) FROM clean_events WHERE event_date = ?", "SELECT count(*) FROM quarantine WHERE event_date = ?"))  # fmt: skip
        con.close()
    return dg.MaterializeResult(metadata={"raw": total, "clean": clean, "quarantined": quarantined})


@dg.asset_check(asset=clean_events, blocking=True)
def clean_has_no_bad_rows(
    context: AssetCheckExecutionContext, wh: Warehouse
) -> dg.AssetCheckResult:
    day = context.partition_key
    with _LOCK:
        con = wh.connect()
        bad = con.execute(
            "SELECT count(*) FROM clean_events WHERE event_date = ? AND (user_id IS NULL OR amount < 0)",
            [day],
        ).fetchone()[0]
        dup = con.execute(
            "SELECT count(*) - count(DISTINCT event_id) FROM clean_events WHERE event_date = ?",
            [day],
        ).fetchone()[0]
        con.close()
    return dg.AssetCheckResult(
        passed=bad == 0 and dup == 0, metadata={"bad_rows": bad, "duplicates": dup}
    )


@dg.asset_check(asset=clean_events)
def quarantine_rate_is_low(
    context: AssetCheckExecutionContext, wh: Warehouse
) -> dg.AssetCheckResult:
    """Non-blocking: a warning when more than 10% of a day's rows are quarantined."""
    day = context.partition_key
    with _LOCK:
        con = wh.connect()
        q = con.execute("SELECT count(*) FROM quarantine WHERE event_date = ?", [day]).fetchone()[0]
        t = max(
            1,
            con.execute("SELECT count(*) FROM raw_events WHERE event_date = ?", [day]).fetchone()[
                0
            ],
        )
        con.close()
    return dg.AssetCheckResult(
        passed=q / t <= 0.10,
        metadata={"rate": round(q / t, 3)},
        severity=dg.AssetCheckSeverity.WARN,
    )


@dg.asset(partitions_def=PARTS, deps=[clean_events], group_name="marts")
def daily_metrics(context: AssetExecutionContext, wh: Warehouse) -> dg.MaterializeResult:
    day = context.partition_key
    with _LOCK:
        con = wh.connect()
        con.execute("DELETE FROM daily_metrics WHERE event_date = ?", [day])
        con.execute(
            "INSERT INTO daily_metrics SELECT event_date, round(sum(amount), 2), count(*), count(DISTINCT user_id) FROM clean_events WHERE event_date = ? GROUP BY event_date",
            [day],
        )
        rev = con.execute(
            "SELECT coalesce(sum(revenue), 0) FROM daily_metrics WHERE event_date = ?", [day]
        ).fetchone()[0]
        con.close()
    return dg.MaterializeResult(metadata={"revenue": rev})


@dg.asset(deps=[daily_metrics], group_name="marts")
def all_time_summary(wh: Warehouse) -> dg.MaterializeResult:
    with _LOCK:
        con = wh.connect()
        days_, rev = con.execute(
            "SELECT count(*), coalesce(sum(revenue), 0) FROM daily_metrics"
        ).fetchone()
        con.close()
    return dg.MaterializeResult(metadata={"days_with_metrics": days_, "revenue": round(rev, 2)})


ASSETS = [raw_events, clean_events, daily_metrics, all_time_summary]
CHECKS = [raw_has_rows, clean_has_no_bad_rows, quarantine_rate_is_low]


def make_defs(db_path: str | Path, outage_days: list[str] | None = None) -> dg.Definitions:
    wh = (
        Warehouse(path=str(db_path))
        if outage_days is None
        else Warehouse(path=str(db_path), outage_days=outage_days)
    )
    return dg.Definitions(assets=ASSETS, asset_checks=CHECKS, resources={"wh": wh})
