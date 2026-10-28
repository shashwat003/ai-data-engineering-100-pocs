"""Scenarios exercised by the demo and the tests. Each takes a running PgServer and returns plain metrics."""

from __future__ import annotations

import random
import tempfile
import threading
import time
from decimal import Decimal

from .consumer import Consumer, PollResult
from .lake import Lake
from .workload import N_ACCOUNTS, START_BALANCE, Workload

TABLES = {"public.orders": ["id"], "public.accounts": ["id"]}
TOTAL = START_BALANCE * N_ACCOUNTS


def new_consumer(
    pg, tables=None, batch: int = 200, slot: str = "cdc_slot"
) -> tuple[Consumer, Lake]:
    lake = Lake(tempfile.mkdtemp(prefix="lake-"))
    return Consumer(pg.connect, lake, tables or dict(TABLES), slot=slot, batch_changes=batch), lake


def start(pg, seed: int = 1, n_orders: int = 60):
    conn = pg.connect(autocommit=True)
    w = Workload(random.Random(seed))
    w.seed(conn, n_orders)
    c, lake = new_consumer(pg)
    c.create_slot()
    return conn, w, c, lake


def first_load_with_concurrent_writes(pg, seed: int = 1) -> dict:
    """Writes happen after the slot exists, before the snapshot, and *while* the snapshot runs."""
    conn, w, c, lake = start(pg, seed)
    for _ in range(30):
        w.txn(conn)
    stop = threading.Event()
    writer_conn = pg.connect(autocommit=True)
    ww = Workload(random.Random(seed + 100), next_id=10_000)  # disjoint id space, same tables
    ww.live = set()
    done = {"n": 0}

    def background():
        while not stop.is_set():
            ww.txn(writer_conn)
            done["n"] += 1

    th = threading.Thread(target=background)
    th.start()
    time.sleep(0.2)
    rows = {t: c.snapshot(t) for t in c.tables}
    stop.set()
    th.join()
    c.drain()
    return {
        "snapshot_rows": rows,
        "background_txns": done["n"],
        "reconcile": {t: c.reconcile(t) for t in c.tables},
    }


def streaming_atomicity(pg, seed: int = 2, txns: int = 400, poll_every: int = 17) -> dict:
    """Poll while the workload runs; after every poll, silver's total balance must still equal the constant total."""
    conn, w, c, lake = start(pg, seed)
    for t in c.tables:
        c.snapshot(t)
    violations, polls, seen_totals = 0, 0, set()
    for i in range(1, txns + 1):
        w.txn(conn)
        if i % poll_every == 0:
            c.poll()
            polls += 1
            total = sum((r["balance"] for r in lake.current("public.accounts")), Decimal(0))
            seen_totals.add(total)
            violations += total != TOTAL
    c.drain()
    clog = lake.changelog("public.orders")
    ghosts = {r["id"] for r in lake.current("public.orders")} & w.ghost_ids
    import duckdb

    ghost_events = duckdb.connect().execute(
        f"SELECT count(*) FROM read_parquet('{lake.bronze_dir('public.orders')}/*.parquet', union_by_name=true) WHERE k__id = ANY(?)", [sorted(w.ghost_ids)]
    ).fetchone()[0]  # fmt: skip
    return {
        "committed": w.committed, "rolled_back": w.rolled_back, "polls": polls,
        "atomicity_violations": violations, "distinct_total_balances_seen": len(seen_totals),
        "ghost_rows_in_silver": len(ghosts), "ghost_events_in_bronze": ghost_events,
        "changelog_events_orders": len(clog),
        "reconcile": {t: c.reconcile(t) for t in c.tables},
    }  # fmt: skip


def crash_and_replay(pg, seed: int = 3) -> dict:
    conn, w, c, lake = start(pg, seed)
    for t in c.tables:
        c.snapshot(t)
    for _ in range(60):
        w.txn(conn)
    lag_before = c.lag_bytes()
    try:
        c.poll(crash_before_advance=True)
        crashed = False
    except RuntimeError:
        crashed = True
    lag_after_crash = c.lag_bytes()
    # restart: a new consumer with a DIFFERENT batch size, so replayed batches overlap the files already written
    c2 = Consumer(pg.connect, lake, dict(TABLES), slot=c.slot, batch_changes=7)
    c2.drain()
    clog = lake.changelog("public.orders")
    return {
        "crashed": crashed, "slot_not_advanced_by_crash": lag_after_crash >= lag_before,
        "bronze_files": len(lake.bronze_files("public.orders")),
        "changelog_events": len(clog), "distinct_events": len(set((a, b) for a, b, _ in clog)),
        "reconcile": {t: c2.reconcile(t) for t in c2.tables},
    }  # fmt: skip


def schema_evolution(pg, seed: int = 4) -> dict:
    conn, w, c, lake = start(pg, seed)
    for t in c.tables:
        c.snapshot(t)
    c.drain()
    conn.execute(
        "ALTER TABLE orders ADD COLUMN channel text"
    )  # nullable, no default: nothing to backfill
    for i in sorted(w.live)[:15]:
        conn.execute("UPDATE orders SET channel = 'web' WHERE id = %s", (i,))
    c.drain()
    plain = c.reconcile("public.orders")
    conn.execute(
        "ALTER TABLE orders ADD COLUMN priority text DEFAULT 'normal'"
    )  # fast default: no WAL event per row
    for _ in range(10):
        w.txn(conn)
    c.drain()
    pitfall = c.reconcile("public.orders")
    repaired_rows = c.snapshot(
        "public.orders"
    )  # re-snapshot repairs rows the stream could not know about
    c.drain()
    repaired = c.reconcile("public.orders")
    return {
        "nullable_column": plain,
        "column_with_default_before_repair": pitfall,
        "resnapshot_rows": repaired_rows,
        "after_repair": repaired,
    }


def slot_lag(pg, seed: int = 5, txns: int = 300) -> dict:
    conn, w, c, lake = start(pg, seed)
    for t in c.tables:
        c.snapshot(t)
    c.drain()
    idle = c.lag_bytes()
    for _ in range(txns):
        w.txn(conn)
    behind = c.lag_bytes()
    t0 = time.perf_counter()
    n = c.drain()
    secs = time.perf_counter() - t0
    return {"idle_lag_bytes": idle, "lag_after_writes_bytes": behind, "lag_after_drain_bytes": c.lag_bytes(),
            "drained_transactions": n, "drain_seconds_measured": round(secs, 3)}  # fmt: skip


def unsupported_type_fails_closed(pg) -> dict:
    conn = pg.connect(autocommit=True)
    conn.execute("CREATE TABLE blobs (id int PRIMARY KEY, payload jsonb)")
    c, lake = new_consumer(pg, {"public.blobs": ["id"]}, slot="blob_slot")
    c.create_slot()
    conn.execute("""INSERT INTO blobs VALUES (1, '{"a": 1}')""")
    try:
        c.poll()
        error = None
    except Exception as e:  # noqa: BLE001 - the point is to record what stopped it
        error = f"{type(e).__name__}: {str(e)[:90]}"
    return {
        "error": error,
        "lag_bytes_after_failure": c.lag_bytes(),
        "bronze_files": len(lake.bronze_files("public.blobs"))
        if (lake.root / "bronze" / "public.blobs").exists()
        else 0,
    }


__all__ = ["PollResult"]
