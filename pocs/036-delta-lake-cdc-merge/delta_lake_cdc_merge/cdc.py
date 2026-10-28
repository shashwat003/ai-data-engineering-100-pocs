"""CDC feed generation and Delta Lake MERGE application, with a pure-Python oracle.

Table `customers`: one row per id. Target columns carry `_lsn` (source log position of the last
applied change) and `_deleted` (tombstone). Deletes are soft so that a late, older event cannot
resurrect a row; `purge_tombstones` removes old tombstones when it is safe.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
from deltalake import CommitProperties, DeltaTable, write_deltalake

TIERS = ("free", "pro", "enterprise")
SCHEMA = pa.schema([("id", pa.int64()), ("name", pa.string()), ("tier", pa.string()),
                    ("balance", pa.float64()), ("_lsn", pa.int64()), ("_deleted", pa.bool_())])  # fmt: skip
EVENT_SCHEMA = pa.schema([("op", pa.string()), ("id", pa.int64()), ("name", pa.string()),
                          ("tier", pa.string()), ("balance", pa.float64()), ("lsn", pa.int64())])  # fmt: skip


@dataclass(frozen=True)
class Event:
    op: str  # I | U | D
    id: int
    name: str | None
    tier: str | None
    balance: float | None
    lsn: int


def generate_events(n_keys: int, n_events: int, seed: int = 1) -> list[Event]:
    """A consistent change history: inserts, updates, deletes and re-inserts, lsn strictly increasing."""
    rng = random.Random(seed)
    alive: dict[int, bool] = {}
    out = []
    for lsn in range(1, n_events + 1):
        k = rng.randint(1, n_keys)
        if not alive.get(k):
            op = "I"
            alive[k] = True
        else:
            op = rng.choices(["U", "D"], [0.85, 0.15])[0]
            if op == "D":
                alive[k] = False
        if op == "D":
            out.append(Event("D", k, None, None, None, lsn))
        else:
            out.append(
                Event(
                    op,
                    k,
                    f"cust-{k}-v{lsn}",
                    rng.choice(TIERS),
                    round(rng.uniform(0, 1000), 2),
                    lsn,
                )
            )
    return out


def deliver(
    events: list[Event], batch_size: int, seed: int = 2, dup_rate: float = 0.1
) -> list[list[Event]]:
    """At-least-once, unordered delivery: batches in order, but shuffled inside and with duplicates."""
    rng = random.Random(seed)
    batches = [events[i : i + batch_size] for i in range(0, len(events), batch_size)]
    out = []
    for b in batches:
        b = list(b) + [e for e in b if rng.random() < dup_rate]
        rng.shuffle(b)
        out.append(b)
    return out


def oracle(events: list[Event]) -> dict[int, tuple]:
    """Ground truth: apply every event once in lsn order; deleted keys are absent."""
    state: dict[int, tuple] = {}
    for e in sorted(events, key=lambda e: e.lsn):
        if e.op == "D":
            state.pop(e.id, None)
        else:
            state[e.id] = (e.name, e.tier, e.balance)
    return state


def _table(events: list[Event]) -> pa.Table:
    cols = {f.name: [getattr(e, f.name) for e in events] for f in EVENT_SCHEMA}
    return pa.table(cols, schema=EVENT_SCHEMA)


def dedupe_latest(events: list[Event]) -> list[Event]:
    """Keep only the highest-lsn event per key: older changes inside the batch are redundant."""
    best: dict[int, Event] = {}
    for e in events:
        if e.id not in best or e.lsn > best[e.id].lsn:
            best[e.id] = e
    return sorted(best.values(), key=lambda e: e.id)


def create(path: str | Path) -> DeltaTable:
    write_deltalake(
        str(path),
        pa.table({f.name: pa.array([], f.type) for f in SCHEMA}, schema=SCHEMA),
        mode="overwrite",
    )
    return DeltaTable(str(path))


def applied_batches(dt: DeltaTable) -> set[str]:
    return {h["batch_id"] for h in dt.history() if "batch_id" in h}


def apply_batch(path: str | Path, batch_id: str, events: list[Event]) -> dict:
    """MERGE one CDC batch. Safe to call again with the same batch (no-op) or an older one."""
    dt = DeltaTable(str(path))
    if batch_id in applied_batches(dt):
        return {"batch_id": batch_id, "skipped": True}
    src = _table(dedupe_latest(events))
    meta = CommitProperties(custom_metadata={"batch_id": batch_id})
    metrics = (
        dt.merge(src, predicate="t.id = s.id", source_alias="s", target_alias="t", commit_properties=meta)
        .when_matched_update(
            updates={"name": "CASE WHEN s.op = 'D' THEN t.name ELSE s.name END",
                     "tier": "CASE WHEN s.op = 'D' THEN t.tier ELSE s.tier END",
                     "balance": "CASE WHEN s.op = 'D' THEN t.balance ELSE s.balance END",
                     "_lsn": "s.lsn", "_deleted": "s.op = 'D'"},
            predicate="s.lsn > t._lsn",
        )
        .when_not_matched_insert(  # a delete for an unseen key still writes a tombstone
            updates={"id": "s.id", "name": "s.name", "tier": "s.tier", "balance": "s.balance",
                     "_lsn": "s.lsn", "_deleted": "s.op = 'D'"})
        .execute()
    )  # fmt: skip
    return {"batch_id": batch_id, "skipped": False, **{k: metrics[k] for k in
            ("num_target_rows_inserted", "num_target_rows_updated", "num_source_rows")}}  # fmt: skip


def current_state(path: str | Path, version: int | None = None) -> dict[int, tuple]:
    dt = DeltaTable(str(path), version=version)
    t = dt.to_pyarrow_table().to_pydict()
    return {
        i: (n, tr, b)
        for i, n, tr, b, d in zip(
            t["id"], t["name"], t["tier"], t["balance"], t["_deleted"], strict=True
        )
        if not d
    }


def purge_tombstones(path: str | Path, older_than_lsn: int) -> int:
    """Hard-delete tombstones whose delete is older than every event that can still arrive.

    Only safe once no batch containing an lsn below `older_than_lsn` can be delivered any more.
    """
    dt = DeltaTable(str(path))
    return dt.delete(f"_deleted = true AND _lsn < {older_than_lsn}")["num_deleted_rows"]


def file_count(path: str | Path) -> int:
    return len(DeltaTable(str(path)).file_uris())
