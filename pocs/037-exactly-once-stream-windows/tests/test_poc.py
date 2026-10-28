import random

import pytest
from exactly_once_windows.broker import Broker, BrokerError
from exactly_once_windows.main import POINTS, collect, load, make_events, random_plan, run, score
from exactly_once_windows.processor import (
    BATCH,
    LATENESS,
    WINDOW,
    Crashes,
    Processor,
    reference,
    run_to_completion,
)


def broker():
    b = Broker()
    for t in ("t", "u"):
        b.create_topic(t)
    return b


# --- broker semantics -------------------------------------------------------------------------------------------
def test_idempotent_producer_ignores_retries_and_rejects_gaps():
    b = broker()
    o0 = b.produce("t", 0, "k", {"v": 1}, 1, pid="p", seq=0)
    assert (
        b.produce("t", 0, "k", {"v": 1}, 1, pid="p", seq=0) == o0
    )  # retry: same offset, nothing appended
    assert b.end_offset("t", 0) == 1
    with pytest.raises(BrokerError, match="out-of-order"):
        b.produce("t", 0, "k", {"v": 2}, 2, pid="p", seq=5)
    b.produce("t", 0, "k", {"v": 2}, 2, pid="p", seq=1)
    assert b.end_offset("t", 0) == 2


def test_transaction_commit_is_atomic_for_records_and_offsets():
    b = broker()
    b.begin("x")
    b.produce("t", 0, "k", {"v": 1}, 1, txn="x")
    b.produce("u", 0, "k", {"v": 2}, 2, txn="x")
    b.send_offsets("x", "g", "t", 0, 7)
    assert (
        b.fetch("t", 0, 0, 10) == [] and b.committed("g", "t", 0) == 0
    )  # nothing visible before commit
    b.commit("x")
    assert (
        len(b.fetch("t", 0, 0, 10)) == 1
        and len(b.fetch("u", 0, 0, 10)) == 1
        and b.committed("g", "t", 0) == 7
    )


def test_abort_hides_records_and_leaves_offsets():
    b = broker()
    b.begin("x")
    b.produce("t", 0, "k", {"v": 1}, 1, txn="x")
    b.send_offsets("x", "g", "t", 0, 3)
    b.abort("x")
    assert b.fetch("t", 0, 0, 10) == [] and b.committed("g", "t", 0) == 0
    assert (
        len(b.fetch("t", 0, 0, 10, read_committed=False)) == 1
    )  # a read_uncommitted reader still sees the aborted record


def test_pending_transaction_blocks_later_records_for_read_committed_readers():
    b = broker()
    b.begin("x")
    b.produce("t", 0, "k", {"v": 1}, 1, txn="x")
    b.produce("t", 0, "k", {"v": 2}, 2)  # non-transactional record after the open transaction
    assert (
        b.fetch("t", 0, 0, 10) == []
    )  # last stable offset: the later record waits behind the open transaction
    b.commit("x")
    assert [r.value["v"] for r in b.fetch("t", 0, 0, 10)] == [1, 2]


def test_fencing_aborts_a_crashed_predecessors_transaction():
    b = broker()
    b.begin("x")
    b.produce("t", 0, "k", {"v": 1}, 1, txn="x")
    b.init_transactions("x")
    assert b.fetch("t", 0, 0, 10) == [] and "x" not in b.open_txn
    b.begin("x")  # the new incarnation can start a transaction
    with pytest.raises(BrokerError):
        b.begin("x")


def test_broker_serialises_values_so_callers_cannot_alias_the_log():
    b = broker()
    v = {"nested": [1, 2]}
    b.produce("t", 0, "k", v, 1)
    v["nested"].append(99)
    got = b.fetch("t", 0, 0, 1)[0]
    got.value["nested"].append(100)
    assert b.fetch("t", 0, 0, 1)[0].value == {"nested": [1, 2]}


# --- processing ---------------------------------------------------------------------------------------------------------
def test_reference_semantics_on_a_tiny_stream():
    totals, late = reference(
        [("A", 10, 1), ("A", 70, 2), ("A", 200, 3), ("A", 15, 4), ("A", 130, 5)]
    )
    assert (
        totals[("A", 0)] == (1, 1) and totals[("A", 60)] == (1, 2) and ("A", 15, 4) in late
    )  # 15 arrives after the watermark passed 0..60


@pytest.mark.parametrize("mode", ["exactly_once", "at_least_once", "at_most_once"])
def test_without_crashes_every_mode_matches_the_reference(mode):
    ev = make_events()
    b = load(ev)
    assert run_to_completion(b, mode) == 0
    assert score(b, ev)["exact"]


def test_late_events_go_to_the_side_topic_and_are_not_counted():
    ev = make_events()
    b = load(ev)
    run_to_completion(b, "exactly_once")
    sc = score(b, ev)
    assert sc["late_events"] == sc["late_expected"] > 0


@pytest.mark.parametrize("point", POINTS)
def test_a_single_crash_at_each_point_keeps_exactly_once(point):
    ev = make_events()
    for step in (1, 7, 24):
        b = load(ev)
        assert run_to_completion(b, "exactly_once", {(step, point)}) == 1
        assert score(b, ev)["exact"], (step, point)


def test_crash_with_open_transaction_exposes_nothing_before_recovery():
    ev = make_events()
    b = load(ev)
    proc = Processor(b, "exactly_once", Crashes({(2, "after_output")}))
    proc.run_once()
    with pytest.raises(Exception, match="crash"):
        proc.run_once()
    visible_before = len(b.fetch("windows", 0, 0, 10**6))
    offset_before = b.committed("agg", "events", 0)
    assert offset_before == BATCH  # the second batch's offset was not committed
    assert len(b.fetch("windows", 0, 0, 10**6)) == visible_before


def test_random_crash_schedules_always_give_exactly_once():
    ev = make_events()
    steps = -(-len(ev) // BATCH)
    rng = random.Random(2024)
    for _ in range(25):
        b = load(ev)
        run_to_completion(b, "exactly_once", random_plan(rng, rng.randint(1, 8), steps))
        assert score(b, ev)["exact"]


def test_at_least_once_duplicates_and_corrupts_after_a_crash():
    ev = make_events()
    bad = 0
    for step in range(2, 24, 3):
        b = load(ev)
        run_to_completion(b, "at_least_once", {(step, "after_output")})
        sc = score(b, ev)
        bad += not sc["exact"]
    assert (
        bad >= 5
    )  # in-memory state is lost on restart: replayed batches give wrong values, and emitted windows can repeat


def test_at_most_once_loses_events_after_a_crash():
    ev = make_events()
    b = load(ev)
    run_to_completion(b, "at_most_once", {(5, "after_commit_offset")})
    sc = score(b, ev)
    assert not sc["exact"] and (sc["windows_missing"] or sc["windows_with_wrong_values"])


def test_output_windows_are_closed_in_order_and_never_reopened():
    ev = make_events()
    b = load(ev)
    run_to_completion(b, "exactly_once", {(4, "before_commit"), (9, "after_output")})
    recs = b.fetch("windows", 0, 0, 10**6)
    seen = set()
    for r in recs:
        k = (r.value["key"], r.value["window_start"])
        assert k not in seen
        seen.add(k)
        assert r.value["window_start"] % WINDOW == 0
    assert collect(b)["late"] == score(b, ev)["late_expected"] and LATENESS < WINDOW


def test_demo_summary():
    r = run(trials=8)
    assert r["modes"]["exactly_once"]["runs_exactly_correct"] == 8
    assert (
        r["modes"]["at_least_once"]["runs_exactly_correct"] < 8
        and r["modes"]["at_most_once"]["total_windows_missing"] > 0
        and r["no_crash_exactly_once"]["exact"]
    )
