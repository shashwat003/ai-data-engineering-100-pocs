import pytest
from delta_lake_cdc_merge.cdc import (
    Event,
    _table,
    apply_batch,
    create,
    current_state,
    dedupe_latest,
    deliver,
    file_count,
    generate_events,
    oracle,
    purge_tombstones,
)
from deltalake import DeltaTable, write_deltalake


def run(tmp_path, events, batch_size=50, seed=2):
    path = tmp_path / "t"
    create(path)
    batches = deliver(events, batch_size, seed)
    for i, b in enumerate(batches):
        apply_batch(path, f"b{i}", b)
    return path, batches


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_unordered_duplicated_delivery_converges_to_the_oracle(tmp_path, seed):
    events = generate_events(60, 1500, seed)
    path, _ = run(tmp_path, events, 100, seed)
    assert current_state(path) == oracle(events)


def test_oracle_semantics():
    ev = [Event("I", 1, "a", "free", 1.0, 1), Event("U", 1, "b", "pro", 2.0, 2), Event("D", 1, None, None, None, 3),
          Event("I", 1, "c", "free", 3.0, 4), Event("I", 2, "x", "pro", 9.0, 5), Event("D", 2, None, None, None, 6)]  # fmt: skip
    assert oracle(ev) == {1: ("c", "free", 3.0)}


def test_generated_history_is_consistent():
    ev = generate_events(20, 500, 9)
    assert [e.lsn for e in ev] == list(range(1, 501))
    ops = {e.op for e in ev}
    assert ops == {"I", "U", "D"}
    seen = {}
    for e in ev:  # no update/delete of a key that is not alive
        if e.op == "I":
            assert not seen.get(e.id)
            seen[e.id] = True
        else:
            assert seen.get(e.id)
            seen[e.id] = e.op != "D"


def test_replaying_a_batch_is_a_noop(tmp_path):
    events = generate_events(30, 300, 1)
    path, batches = run(tmp_path, events)
    v = DeltaTable(str(path)).version()
    assert apply_batch(path, "b0", batches[0]) == {"batch_id": "b0", "skipped": True}
    assert DeltaTable(str(path)).version() == v


def test_older_event_cannot_overwrite_newer_state_even_under_a_new_batch_id(tmp_path):
    path = tmp_path / "t"
    create(path)
    apply_batch(path, "new", [Event("U", 1, "new", "pro", 2.0, 10)])
    apply_batch(path, "stale-redelivery", [Event("I", 1, "old", "free", 1.0, 3)])
    assert current_state(path) == {1: ("new", "pro", 2.0)}


def test_late_insert_does_not_resurrect_a_deleted_key(tmp_path):
    path = tmp_path / "t"
    create(path)
    apply_batch(path, "d", [Event("D", 7, None, None, None, 20)])  # delete arrives first
    apply_batch(path, "i", [Event("I", 7, "x", "free", 1.0, 5)])  # older insert arrives later
    assert current_state(path) == {}
    apply_batch(path, "re", [Event("I", 7, "y", "pro", 3.0, 30)])  # genuine re-insert
    assert current_state(path) == {7: ("y", "pro", 3.0)}


def test_dedupe_keeps_highest_lsn_per_key():
    ev = [
        Event("U", 1, "a", "free", 1.0, 5),
        Event("U", 1, "b", "free", 1.0, 9),
        Event("D", 2, None, None, None, 3),
    ]
    assert {e.id: e.lsn for e in dedupe_latest(ev)} == {1: 9, 2: 3}


def test_time_travel_returns_each_historic_state(tmp_path):
    events = generate_events(40, 600, 3)
    path = tmp_path / "t"
    create(path)
    batches = deliver(events, 100, 3)
    for i, b in enumerate(batches):
        apply_batch(path, f"b{i}", b)
        assert current_state(path, version=i + 1) == oracle(
            [e for bb in batches[: i + 1] for e in bb]
        )


def test_tombstones_are_purged_only_below_the_watermark(tmp_path):
    events = generate_events(30, 600, 4)
    path, _ = run(tmp_path, events)
    before = current_state(path)
    all_t = DeltaTable(str(path)).to_pyarrow_table().to_pydict()
    old = sum(d and lsn < 300 for d, lsn in zip(all_t["_deleted"], all_t["_lsn"], strict=True))
    assert purge_tombstones(path, 300) == old
    assert current_state(path) == before  # live data untouched


def test_compaction_reduces_files_without_losing_rows(tmp_path):
    log = tmp_path / "log"
    for i in range(12):
        write_deltalake(str(log), _table([Event("I", i, "n", "free", 1.0, i)]), mode="append")
    assert file_count(log) == 12
    DeltaTable(str(log)).optimize.compact()
    assert file_count(log) == 1 and DeltaTable(str(log)).to_pyarrow_table().num_rows == 12


def test_nulls_and_empty_batch(tmp_path):
    path = tmp_path / "t"
    create(path)
    apply_batch(path, "e", [])
    apply_batch(path, "n", [Event("I", 1, None, None, None, 1)])
    assert current_state(path) == {1: (None, None, None)}
