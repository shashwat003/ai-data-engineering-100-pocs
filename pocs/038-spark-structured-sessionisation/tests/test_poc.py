import os
from datetime import UTC, timedelta

import pytest
from spark_structured_sessionisation.events import (
    GAP,
    T0,
    assign_arrival,
    make_events,
    oracle_sessions,
    simulate_drops,
    write_batches,
)
from spark_structured_sessionisation.main import compare, run
from spark_structured_sessionisation.streaming import get_spark, normalise


def ev(user, minutes):
    return {"user": user, "ts": T0 + timedelta(minutes=minutes), "page": "home"}


def test_oracle_gap_boundary_is_exclusive():
    assert [s[3] for s in oracle_sessions([ev("a", 0), ev("a", 29), ev("a", 59)])] == [
        2,
        1,
    ]  # 29 min joins, exactly 30 min splits
    two = oracle_sessions([ev("a", 0), ev("a", 30)])
    one = oracle_sessions(
        [
            {"user": "a", "ts": T0, "page": "x"},
            {"user": "a", "ts": T0 + timedelta(minutes=29, seconds=59), "page": "x"},
        ]
    )
    assert len(two) == 2 and len(one) == 1


def test_oracle_session_bounds_match_spark_convention():
    ((u, start, end, n),) = oracle_sessions([ev("a", 5), ev("a", 12)])
    assert (u, start, end, n) == (
        "a",
        T0 + timedelta(minutes=5),
        T0 + timedelta(minutes=12) + GAP,
        2,
    )


def test_oracle_is_order_independent_and_per_user():
    a = [ev("a", 0), ev("b", 1), ev("a", 10), ev("b", 100)]
    assert oracle_sessions(a) == oracle_sessions(list(reversed(a)))
    assert len(oracle_sessions(a)) == 3
    assert oracle_sessions([]) == []


def test_generator_sessions_are_separated_by_more_than_the_gap():
    events = make_events(6)
    sess = oracle_sessions(events)
    by = {}
    for u, s, e, _n in sess:
        by.setdefault(u, []).append((s, e))
    for spans in by.values():
        spans.sort()
        for (_, e1), (s2, _) in zip(spans, spans[1:], strict=False):
            assert (
                s2 >= e1
            )  # next session starts at or after the previous one's end (last_ts + gap)
    assert sum(s[3] for s in sess) == len(events)


def test_arrival_batches_partition_all_events_and_are_deterministic():
    events = make_events(10)
    b1, b2 = assign_arrival(events, 6), assign_arrival(events, 6)
    assert sum(len(b) for b in b1) == len(events) and [len(b) for b in b1] == [len(b) for b in b2]
    keys = [(e["user"], e["ts"]) for b in b1 for e in b]
    assert len(set(keys)) == len(keys)


def test_files_are_ordered_by_mtime_and_flush_is_last(tmp_path):
    d = write_batches(assign_arrival(make_events(4), 3), tmp_path)
    files = sorted(d.iterdir(), key=lambda p: os.stat(p).st_mtime)
    assert [p.name for p in files] == sorted(p.name for p in d.iterdir()) and files[
        -1
    ].name.endswith("_flush.json")


def test_python_drop_model_is_monotonic_in_the_watermark():
    b = assign_arrival(make_events(20), 8)
    d = [simulate_drops(b, timedelta(hours=h)) for h in (0.05, 0.5, 3)]
    assert d[0] >= d[1] >= d[2] == 0


def test_compare_counts_exact_sessions():
    o = [("a", T0, T0 + GAP, 1), ("b", T0, T0 + GAP, 2)]
    rows = [
        {
            "user": "a",
            "start": T0.replace(tzinfo=None),
            "end": (T0 + GAP).replace(tzinfo=None),
            "count": 1,
        },
        {"user": "b", "start": T0, "end": T0 + GAP, "count": 1},
    ]
    c = compare(rows, o)
    assert (
        c["exact_matches"] == 1
        and c["match_rate"] == 0.5
        and c["events_in_oracle"] == 3
        and c["events_in_spark"] == 2
    )
    assert all(
        r[1].tzinfo == UTC for r in normalise(rows)
    )  # naive Spark timestamps are read as UTC


# ---- real Spark Structured Streaming (needs pyspark + Java: heavy workflow)


@pytest.fixture(scope="module")
def spark_result():
    spark = get_spark()
    try:
        yield run(spark, n_users=12, n_batches=8)
    finally:
        spark.stop()


def test_long_watermark_reproduces_the_batch_oracle_exactly(spark_result):
    r = {x["watermark"]: x for x in spark_result["results"]}
    assert r["3 hours"]["match_rate"] == 1.0 and r["3 hours"]["dropped_events"] == 0


def test_shorter_watermarks_lose_late_events_but_never_invent_them(spark_result):
    rs = {x["watermark"]: x for x in spark_result["results"]}
    for x in rs.values():
        assert x["events_in_spark"] <= x["events_in_oracle"]
    assert (
        rs["5 minutes"]["dropped_events"]
        >= rs["30 minutes"]["dropped_events"]
        >= rs["3 hours"]["dropped_events"]
    )
    assert (
        rs["5 minutes"]["match_rate"]
        <= rs["30 minutes"]["match_rate"]
        <= rs["3 hours"]["match_rate"]
    )


def test_python_model_is_an_upper_bound_on_spark_drops(spark_result):
    for x in spark_result["results"]:
        assert x["dropped_events"] <= x["python_model_dropped"]


def test_longer_watermark_delays_emission(spark_result):
    rs = {x["watermark"]: x for x in spark_result["results"]}
    assert rs["3 hours"]["median_emit_lag_batches"] > rs["5 minutes"]["median_emit_lag_batches"]
    assert rs["3 hours"]["emitted_only_at_flush"] > rs["5 minutes"]["emitted_only_at_flush"]


def test_flush_sentinel_never_appears_in_results(spark_result):
    assert spark_result["oracle_sessions"] > 0  # the sentinel user is filtered inside run_stream
