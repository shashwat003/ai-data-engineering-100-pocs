from decimal import Decimal

import pytest
from postgres_logical_cdc_to_lake import scenarios as S
from postgres_logical_cdc_to_lake.consumer import lsn_int
from postgres_logical_cdc_to_lake.decode import (
    UnsupportedChange,
    envelope,
    parse_change,
    parse_tuple,
)
from postgres_logical_cdc_to_lake.lake import Lake
from postgres_logical_cdc_to_lake.pgserver import PgServer, find_bin

# ---------- parsing and lake logic: no server needed ----------


def test_insert_with_quotes_unicode_null_and_empty_string():
    ch = parse_change(
        "table public.orders: INSERT: id[integer]:1 status[text]:'it''s \"x\"' amount[numeric]:10.50 note[text]:null"
    )
    assert ch.op == "c" and ch.before is None
    assert ch.after == {"id": 1, "status": 'it\'s "x"', "amount": Decimal("10.50"), "note": None}
    assert parse_tuple("a[text]:'' b[text]:'null' c[text]:null")[0] == {
        "a": "",
        "b": "null",
        "c": None,
    }
    assert parse_tuple("a[text]:'ünï ✓\nline2'")[0]["a"] == "ünï ✓\nline2"


def test_string_that_looks_like_a_column_is_not_parsed_as_one():
    assert parse_tuple("a[text]:'x[integer]:1 y[text]:2' b[integer]:5")[0] == {
        "a": "x[integer]:1 y[text]:2",
        "b": 5,
    }


def test_update_with_and_without_old_key_and_delete():
    u = parse_change(
        "table public.t: UPDATE: old-key: id[integer]:1 new-tuple: id[integer]:2 v[text]:'a'"
    )
    assert u.op == "u" and u.before == {"id": 1} and u.after == {"id": 2, "v": "a"}
    u2 = parse_change("table public.t: UPDATE: id[integer]:1 v[text]:'b'")
    assert u2.before is None and u2.after == {"id": 1, "v": "b"}
    d = parse_change("table public.t: DELETE: id[integer]:9")
    assert d.op == "d" and d.before == {"id": 9} and d.after is None


@pytest.mark.parametrize("line", [
    "table public.t: INSERT: id[integer]:1 p[jsonb]:'{}'",
    "table public.t: UPDATE: id[integer]:1 v[text]:unchanged-toast-datum",
    "table public.t: INSERT: id[integer]:1 v[text]:'unterminated",
    "BEGIN 5",
    "garbage",
])  # fmt: skip
def test_anything_unparseable_raises_instead_of_guessing(line):
    with pytest.raises(UnsupportedChange):
        parse_change(line)


def test_envelope_has_the_debezium_shape():
    e = envelope(
        parse_change("table public.t: DELETE: id[integer]:9"),
        lsn=5,
        xid=3,
        ordinal=2,
        commit_ts="ts",
    )
    assert e["op"] == "d" and e["after"] is None and e["before"] == {"id": 9}
    assert e["source"] == {"db": "postgres", "table": "public.t", "lsn": 5, "txId": 3, "ordinal": 2}


def test_lsn_parsing():
    assert lsn_int("0/10") == 16 and lsn_int("1/0") == 1 << 32


def ev(op, lsn, ordinal, after=None, before=None):
    return {
        "op": op,
        "before": before,
        "after": after,
        "source": {"lsn": lsn, "txId": 1, "ordinal": ordinal},
        "ts": "t",
    }


def test_silver_keeps_newest_event_per_key_and_drops_deletes(tmp_path):
    lake = Lake(tmp_path)
    types = {"id": "integer", "v": "text"}
    lake.append_changes(
        "t",
        ["id"],
        [ev("c", 1, 0, {"id": 1, "v": "a"}), ev("c", 1, 1, {"id": 2, "v": "x"})],
        types,
        "f1",
    )
    lake.append_changes(
        "t",
        ["id"],
        [ev("u", 2, 0, {"id": 1, "v": "b"}), ev("d", 3, 0, before={"id": 2})],
        types,
        "f2",
    )
    assert lake.rebuild_current("t") == 1 and lake.current("t") == [{"id": 1, "v": "b"}]


def test_silver_is_independent_of_file_order_and_duplicate_events(tmp_path):
    lake = Lake(tmp_path)
    types = {"id": "integer", "v": "text"}
    new = ev("u", 5, 0, {"id": 1, "v": "new"})
    lake.append_changes("t", ["id"], [new], types, "a_newest")
    lake.append_changes("t", ["id"], [ev("c", 1, 0, {"id": 1, "v": "old"})], types, "z_oldest")
    lake.append_changes(
        "t", ["id"], [new], types, "dup"
    )  # the same event replayed in an overlapping file
    lake.rebuild_current("t")
    assert lake.current("t") == [{"id": 1, "v": "new"}] and len(lake.changelog("t")) == 3


def test_ordering_inside_one_transaction_uses_the_ordinal(tmp_path):
    lake = Lake(tmp_path)
    types = {"id": "integer", "v": "text"}
    lake.append_changes(
        "t",
        ["id"],
        [ev("c", 7, 0, {"id": 1, "v": "first"}), ev("u", 7, 1, {"id": 1, "v": "second"})],
        types,
        "f",
    )
    lake.rebuild_current("t")
    assert lake.current("t")[0]["v"] == "second"


def test_new_column_appears_and_old_rows_read_as_null(tmp_path):
    lake = Lake(tmp_path)
    lake.append_changes(
        "t", ["id"], [ev("c", 1, 0, {"id": 1, "v": "a"})], {"id": "integer", "v": "text"}, "f1"
    )
    lake.append_changes(
        "t",
        ["id"],
        [ev("c", 2, 0, {"id": 2, "v": "b", "extra": "x"})],
        {"id": "integer", "v": "text", "extra": "text"},
        "f2",
    )
    lake.rebuild_current("t")
    assert lake.current("t") == [
        {"id": 1, "v": "a", "extra": None},
        {"id": 2, "v": "b", "extra": "x"},
    ]


def test_numeric_keeps_exact_decimal_values(tmp_path):
    lake = Lake(tmp_path)
    lake.append_changes(
        "t",
        ["id"],
        [ev("c", 1, 0, {"id": 1, "amt": Decimal("0.10")})],
        {"id": "integer", "amt": "numeric"},
        "f",
    )
    lake.rebuild_current("t")
    assert lake.current("t")[0]["amt"] == Decimal("0.1")


# ---------- integration with a real PostgreSQL server ----------

needs_pg = pytest.mark.skipif(find_bin() is None, reason="PostgreSQL server binaries not installed")


@pytest.fixture
def pg():
    with PgServer() as server:
        yield server


@needs_pg
def test_first_load_converges_despite_writes_during_the_snapshot(pg):
    r = S.first_load_with_concurrent_writes(pg)
    assert r["background_txns"] > 0
    for rec in r["reconcile"].values():
        assert rec["missing_in_lake"] == rec["extra_in_lake"] == rec["differing"] == 0


@needs_pg
def test_transactions_are_atomic_and_rollbacks_leave_no_trace(pg):
    r = S.streaming_atomicity(pg, txns=250)
    assert r["rolled_back"] > 5 and r["polls"] > 5
    assert r["atomicity_violations"] == 0 and r["distinct_total_balances_seen"] == 1
    assert r["ghost_rows_in_silver"] == 0 and r["ghost_events_in_bronze"] == 0
    assert all(
        v["differing"] == v["missing_in_lake"] == v["extra_in_lake"] == 0
        for v in r["reconcile"].values()
    )


@needs_pg
def test_crash_before_advance_replays_without_duplicating_state(pg):
    r = S.crash_and_replay(pg)
    assert r["crashed"] and r["slot_not_advanced_by_crash"]
    assert (
        r["changelog_events"] > r["distinct_events"]
    )  # overlapping replay really did duplicate events in bronze
    assert all(
        v["differing"] == v["missing_in_lake"] == v["extra_in_lake"] == 0
        for v in r["reconcile"].values()
    )


@needs_pg
def test_schema_evolution_and_the_default_value_pitfall(pg):
    r = S.schema_evolution(pg)
    assert r["nullable_column"]["differing"] == 0
    assert (
        r["column_with_default_before_repair"]["differing"] > 0
    )  # the stream cannot know rows that never changed
    assert (
        r["after_repair"]["differing"]
        == r["after_repair"]["missing_in_lake"]
        == r["after_repair"]["extra_in_lake"]
        == 0
    )


@needs_pg
def test_slot_lag_grows_with_writes_and_returns_to_zero_after_draining(pg):
    r = S.slot_lag(pg, txns=120)
    assert r["lag_after_writes_bytes"] > r["idle_lag_bytes"] and r["lag_after_drain_bytes"] == 0


@needs_pg
def test_unsupported_type_stops_the_consumer_before_advancing_the_slot(pg):
    r = S.unsupported_type_fails_closed(pg)
    assert r["error"] and "jsonb" in r["error"]
    assert r["lag_bytes_after_failure"] > 0 and r["bronze_files"] == 0


@needs_pg
def test_polling_an_idle_slot_is_a_noop(pg):
    conn, w, c, lake = S.start(pg)
    for t in c.tables:
        c.snapshot(t)
    c.drain()
    files = lake.bronze_files("public.orders")
    r = c.poll()
    assert r.transactions == 0 and lake.bronze_files("public.orders") == files


@needs_pg
def test_server_binds_no_tcp_port(pg):
    import socket

    with pytest.raises(OSError), socket.create_connection(("127.0.0.1", pg.port), timeout=0.5):
        pass
