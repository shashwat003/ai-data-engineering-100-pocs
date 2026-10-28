"""POC 038: sessionisation with Structured Streaming; how the watermark trades completeness for latency."""

from __future__ import annotations

import os
import tempfile
from datetime import timedelta
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .events import GAP, assign_arrival, make_events, oracle_sessions, simulate_drops, write_batches
from .streaming import get_spark, normalise, run_stream

POC_DIR = Path(__file__).resolve().parent.parent


def compare(spark_rows, oracle) -> dict:
    s, o = set(normalise(spark_rows)), set(oracle)
    exact = len(s & o)
    return {
        "sessions_oracle": len(o),
        "sessions_spark": len(s),
        "exact_matches": exact,
        "match_rate": round(exact / len(o), 3),
        "events_in_oracle": sum(x[3] for x in o),
        "events_in_spark": sum(x[3] for x in s),
    }


def run(spark, watermarks=("5 minutes", "30 minutes", "3 hours"), n_users=30, n_batches=12) -> dict:
    events = make_events(n_users)
    oracle = oracle_sessions(events, GAP)
    batches = assign_arrival(events, n_batches)
    results = []
    with tempfile.TemporaryDirectory() as t:
        for wm in watermarks:
            d = write_batches(batches, Path(t) / f"in_{wm.replace(' ', '_')}")
            rows = run_stream(spark, d, Path(t) / f"ck_{wm.replace(' ', '_')}", "30 minutes", wm)
            c = compare(rows, oracle)
            c["watermark"] = wm
            c["dropped_events"] = c["events_in_oracle"] - c["events_in_spark"]
            hours = {"5 minutes": 5 / 60, "30 minutes": 0.5, "3 hours": 3.0}[wm]
            c["python_model_dropped"] = simulate_drops(batches, timedelta(hours=hours))
            arrival = {(e["user"], e["ts"]): i for i, b in enumerate(batches) for e in b}
            lat = []
            for r in rows:
                last = max(
                    (
                        i
                        for (u, ts), i in arrival.items()
                        if u == r["user"]
                        and r["start"].replace(tzinfo=ts.tzinfo)
                        <= ts
                        < r["end"].replace(tzinfo=ts.tzinfo) - GAP + timedelta(seconds=1)
                    ),
                    default=None,
                )
                if last is not None:
                    lat.append(r["batch"] - last)
            lat.sort()
            c["median_emit_lag_batches"] = lat[len(lat) // 2] if lat else None
            c["emitted_only_at_flush"] = round(
                sum(r["batch"] >= len(batches) for r in rows) / max(1, len(rows)), 3
            )
            results.append(c)
    return {
        "n_events": len(events),
        "n_batches": len(batches),
        "gap": "30 minutes",
        "oracle_sessions": len(oracle),
        "results": results,
    }


def main() -> None:
    quick = bool(os.environ.get("DEMO_QUICK"))
    spark = get_spark()
    try:
        res = run(spark, n_users=8 if quick else 30, n_batches=6 if quick else 12)
    finally:
        spark.stop()
    write_json(POC_DIR / "results" / "results.json", res)
    rows = [
        (
            r["watermark"],
            r["sessions_spark"],
            r["sessions_oracle"],
            r["match_rate"],
            r["dropped_events"],
            r["python_model_dropped"],
            r["median_emit_lag_batches"],
            r["emitted_only_at_flush"],
        )
        for r in res["results"]
    ]
    md = (
        f"# Results: Structured Streaming sessionisation ({res['n_events']} events, {res['n_batches']} micro-batches, 30-minute gap)\n\n"
        "The oracle is a plain-Python batch sessionisation of all events. Events arrive out of order: about 6% 10-50 minutes late and 1% 2-3 hours late. "
        "'Dropped events' are events inside oracle sessions that Spark discarded because they arrived behind the watermark.\n\n"
        + markdown_table(
            [
                "watermark",
                "sessions emitted",
                "oracle sessions",
                "sessions identical to oracle",
                "events dropped by Spark",
                "drops predicted by Python model",
                "median lag (micro-batches) from last event arrival to emission",
                "share emitted only by the final flush",
            ],
            rows,
        )
        + (
            "\n\nDEMO_QUICK smoke run (fewer users and batches): numbers are not the published ones.\n"
            if quick
            else "\n"
        )
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
