"""An in-process log broker with Kafka-style semantics: partitions, idempotent producers, transactions and read_committed reads.

SIMULATED: no Kafka or Redpanda is used. The point is to test processing guarantees (exactly-once effect under crashes), so the
broker implements only what that needs: append-only partitions, producer-id/sequence de-duplication, atomic commit of produced
records together with consumer-group offsets, transaction fencing on restart, and last-stable-offset reads.
"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass


class BrokerError(RuntimeError):
    pass


@dataclass
class Record:
    offset: int
    key: str
    value: dict
    ts: int
    state: str = "committed"  # committed | pending | aborted
    txn: str | None = None


class Broker:
    def __init__(self):
        self.logs: dict[tuple[str, int], list[Record]] = {}
        self.committed_offsets: dict[tuple[str, str, int], int] = {}
        self.producer_state: dict[tuple[str, str, int], dict] = {}
        self.open_txn: dict[str, dict] = {}
        self.lock = threading.RLock()

    def create_topic(self, name: str, partitions: int = 1) -> None:
        for p in range(partitions):
            self.logs.setdefault((name, p), [])

    # --- producing ------------------------------------------------------------------------------------
    def produce(
        self,
        topic: str,
        part: int,
        key: str,
        value: dict,
        ts: int,
        *,
        pid: str | None = None,
        seq: int | None = None,
        txn: str | None = None,
    ) -> int:
        with self.lock:
            log = self.logs[(topic, part)]
            state = (
                self.producer_state.setdefault((pid, topic, part), {"last": -1, "offsets": {}})
                if pid is not None
                else None
            )
            if state is not None and seq is not None:
                if (
                    seq <= state["last"]
                ):  # duplicate retry of an already appended record: idempotent no-op
                    return state["offsets"][seq]
                if seq != state["last"] + 1:
                    raise BrokerError(f"out-of-order sequence {seq} (expected {state['last'] + 1})")
            rec = Record(
                len(log),
                key,
                copy.deepcopy(value),
                ts,  # serialise on write: callers must not share memory with the log
                "pending" if txn else "committed",
                txn,
            )
            if state is not None and seq is not None:
                state["last"] = seq
                state["offsets"][seq] = rec.offset
            log.append(rec)
            if txn:
                self.open_txn[txn]["records"].append(rec)
            return rec.offset

    # --- transactions -----------------------------------------------------------------------------------
    def init_transactions(self, txn_id: str) -> None:
        """Fencing: starting a producer with this id aborts whatever its crashed predecessor left open."""
        with self.lock:
            if txn_id in self.open_txn:
                self.abort(txn_id)

    def begin(self, txn_id: str) -> None:
        with self.lock:
            if txn_id in self.open_txn:
                raise BrokerError(f"transaction {txn_id} already open")
            self.open_txn[txn_id] = {"records": [], "offsets": {}}

    def send_offsets(
        self, txn_id: str, group: str, topic: str, part: int, next_offset: int
    ) -> None:
        with self.lock:
            self.open_txn[txn_id]["offsets"][(group, topic, part)] = next_offset

    def commit(self, txn_id: str) -> None:
        """Atomic: all records become visible and all offsets advance together, or nothing changes."""
        with self.lock:
            t = self.open_txn.pop(txn_id)
            for r in t["records"]:
                r.state = "committed"
            self.committed_offsets.update(t["offsets"])

    def abort(self, txn_id: str) -> None:
        with self.lock:
            t = self.open_txn.pop(txn_id, None)
            for r in (t or {"records": []})["records"]:
                r.state = "aborted"

    # --- consuming ----------------------------------------------------------------------------------------
    def commit_offset(self, group: str, topic: str, part: int, next_offset: int) -> None:
        with self.lock:
            self.committed_offsets[(group, topic, part)] = next_offset

    def committed(self, group: str, topic: str, part: int) -> int:
        return self.committed_offsets.get((group, topic, part), 0)

    def fetch(
        self, topic: str, part: int, offset: int, limit: int, *, read_committed: bool = True
    ) -> list[Record]:
        with self.lock:
            out = []
            for r in self.logs[(topic, part)][offset:]:
                if read_committed and r.state == "pending":
                    break  # last stable offset: nothing after an open transaction is readable yet
                if read_committed and r.state == "aborted":
                    continue
                out.append(copy.deepcopy(r))  # serialise on read as well
                if len(out) >= limit:
                    break
            return out

    def end_offset(self, topic: str, part: int) -> int:
        return len(self.logs[(topic, part)])
