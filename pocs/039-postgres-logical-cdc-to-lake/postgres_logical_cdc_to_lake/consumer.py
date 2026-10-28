"""Consume a PostgreSQL logical replication slot into the lake with at-least-once delivery and idempotent effects.

Per poll: PEEK the slot (does not consume) -> group whole committed transactions -> write bronze atomically -> rebuild silver ->
only then ADVANCE the slot. A crash anywhere before the advance just replays the same transactions; bronze file names and the
silver dedup make the replay harmless. Transactions are never split across a silver rebuild, so readers of silver never see
half of a transaction.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import psycopg

from .decode import UnsupportedChange, envelope, parse_change
from .lake import Lake

BEGIN = re.compile(r"^BEGIN (\d+)$")
COMMIT = re.compile(r"^COMMIT (\d+) \(at (.*)\)$")


def lsn_int(lsn: str) -> int:
    hi, lo = lsn.split("/")
    return (int(hi, 16) << 32) | int(lo, 16)


@dataclass
class PollResult:
    transactions: int = 0
    events: int = 0
    last_lsn: str | None = None
    files: list[str] = field(default_factory=list)


class Consumer:
    def __init__(
        self,
        connect: Callable[..., psycopg.Connection],
        lake: Lake,
        tables: dict[str, list[str]],
        slot: str = "cdc_slot",
        batch_changes: int = 200,
    ):
        """tables: 'schema.table' -> primary key column names."""
        self.connect, self.lake, self.tables, self.slot, self.batch = (
            connect,
            lake,
            tables,
            slot,
            batch_changes,
        )
        self.state_path = lake.root / "_state.json"

    # --- setup ----------------------------------------------------------------------------------------
    def create_slot(self) -> str:
        with self.connect(autocommit=True) as c:
            row = c.execute(
                "SELECT slot_name FROM pg_replication_slots WHERE slot_name = %s", (self.slot,)
            ).fetchone()
            if row:
                return "exists"
            return str(
                c.execute(
                    "SELECT lsn::text FROM pg_create_logical_replication_slot(%s, 'test_decoding')",
                    (self.slot,),
                ).fetchone()[0]
            )

    def snapshot(self, table: str) -> int:
        """Copy the table as of one consistent point and tag it with an LSN read *before* that point, so every change not in
        the snapshot is still in the stream's future. Used for the first load and for repairs; returns the row count."""
        with self.connect() as c:
            c.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            l0 = lsn_int(c.execute("SELECT pg_current_wal_lsn()::text").fetchone()[0])
            cur = c.execute(f"SELECT * FROM {table}")  # noqa: S608 - table names come from the trusted config
            cols = [d.name for d in cur.description]
            oids = {d.name: d.type_code for d in cur.description}
            rows = cur.fetchall()
            tmap = {
                r[0]: r[1]
                for r in c.execute(
                    "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema||'.'||table_name = %s",
                    (table,),
                )
            }
            c.execute("COMMIT")
        del oids
        events = [
            {
                "op": "r",
                "before": None,
                "after": dict(zip(cols, r, strict=True)),
                "source": {"lsn": l0, "txId": 0, "ordinal": i, "table": table},
                "ts": "snapshot",
            }
            for i, r in enumerate(rows)
        ]
        if events:
            self.lake.append_changes(
                table, self.tables[table], events, {c: tmap[c] for c in cols}, f"snapshot_{l0:016x}"
            )
        self.lake.rebuild_current(table) if events else None
        return len(rows)

    # --- streaming ------------------------------------------------------------------------------------
    def poll(self, *, crash_before_advance: bool = False) -> PollResult:
        res = PollResult()
        with self.connect(autocommit=True) as c:
            rows = c.execute(
                "SELECT lsn::text, xid::text, data FROM pg_logical_slot_peek_changes(%s, NULL, %s, 'include-xids', '1', 'include-timestamp', '1', 'skip-empty-xacts', '1')",
                (self.slot, self.batch),
            ).fetchall()
            per_table: dict[str, list[dict]] = {}
            types: dict[str, dict] = {}
            pending: list = []
            cur_xid = None
            first = last = None
            for lsn, xid, data in rows:
                if m := BEGIN.match(data):
                    cur_xid, pending = int(m.group(1)), []
                    continue
                if m := COMMIT.match(data):
                    commit_lsn = lsn_int(lsn)
                    for ordinal, ch in enumerate(pending):
                        per_table.setdefault(ch.table, []).append(
                            envelope(
                                ch,
                                lsn=commit_lsn,
                                xid=int(xid),
                                ordinal=ordinal,
                                commit_ts=m.group(2),
                            )
                        )
                        types.setdefault(ch.table, {}).update(ch.types)
                    res.transactions += 1
                    res.events += len(pending)
                    first = first or lsn
                    last = lsn
                    cur_xid = None
                    continue
                if cur_xid is None:
                    raise UnsupportedChange(f"change outside a transaction: {data[:60]!r}")
                try:
                    ch = parse_change(data)
                except UnsupportedChange as e:
                    raise UnsupportedChange(
                        f"{e} (at {lsn}); stopping before the slot is advanced"
                    ) from e
                if ch.table in self.tables:
                    pending.append(ch)
            if not res.transactions:
                return res
            for table, events in per_table.items():
                name = f"changes_{lsn_int(first):016x}_{lsn_int(last):016x}"
                res.files.append(
                    str(
                        self.lake.append_changes(
                            table, self.tables[table], events, types[table], name
                        )
                    )
                )
            for table in per_table:
                self.lake.rebuild_current(table)
            if crash_before_advance:
                raise RuntimeError(
                    "injected crash after the lake write and before the slot advance"
                )
            c.execute("SELECT pg_replication_slot_advance(%s, %s::pg_lsn)", (self.slot, last))
            res.last_lsn = last
            self.state_path.write_text(json.dumps({"confirmed_lsn": last}))
        return res

    def drain(self, max_polls: int = 1000) -> int:
        n = 0
        for _ in range(max_polls):
            r = self.poll()
            if not r.transactions:
                break
            n += r.transactions
        return n

    # --- operations -----------------------------------------------------------------------------------
    def lag_bytes(self) -> int:
        with self.connect(autocommit=True) as c:
            row = c.execute(
                "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::bigint FROM pg_replication_slots WHERE slot_name = %s",
                (self.slot,),
            ).fetchone()
        return int(row[0])

    def reconcile(self, table: str) -> dict:
        """Compare the source table with silver, row by row, ignoring row order."""
        key = self.tables[table]
        with self.connect() as c:
            cur = c.execute(f"SELECT * FROM {table}")  # noqa: S608
            cols = [d.name for d in cur.description]
            src = {
                tuple(r[cols.index(k)] for k in key): dict(zip(cols, r, strict=True))
                for r in cur.fetchall()
            }
        lake = {tuple(r[k] for k in key): r for r in self.lake.current(table)}
        missing = [k for k in src if k not in lake]
        extra = [k for k in lake if k not in src]
        differing = [k for k in src if k in lake and any(src[k][c] != lake[k].get(c) for c in cols)]
        return {
            "source_rows": len(src),
            "lake_rows": len(lake),
            "missing_in_lake": len(missing),
            "extra_in_lake": len(extra),
            "differing": len(differing),
        }
