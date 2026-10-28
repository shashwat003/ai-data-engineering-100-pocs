"""Tumbling-window aggregation over an event-time stream, in three delivery modes, with deterministic crash injection.

state: {(key, window_start): [count, sum]}; watermark = max event time seen - allowed lateness. A window is emitted (once) when
the watermark passes its end; events for already-closed windows go to a `late` topic. In `exactly_once` mode the state snapshot,
the output records, the late records and the consumer offset are committed in ONE transaction, so a crash at any point leaves
either the old consistent state or the new one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .broker import Broker

WINDOW = 60
LATENESS = 30
BATCH = 25


class Crash(RuntimeError):
    """Injected process failure."""


@dataclass
class Crashes:
    """Crash after step number n at a named point, e.g. {(3, 'after_output')}. Each entry fires once."""

    plan: set[tuple[int, str]]
    fired: set[tuple[int, str]] | None = None

    def hit(self, step: int, point: str) -> None:
        self.fired = self.fired if self.fired is not None else set()
        if (step, point) in self.plan and (step, point) not in self.fired:
            self.fired.add((step, point))
            raise Crash(f"crash at step {step} {point}")


class Processor:
    def __init__(
        self,
        broker: Broker,
        mode: str,
        crashes: Crashes | None = None,
        in_topic="events",
        out_topic="windows",
        late_topic="late",
        state_topic="state",
        group="agg",
        pid="proc-1",
    ):
        assert mode in ("exactly_once", "at_least_once", "at_most_once")
        self.b, self.mode, self.crashes = broker, mode, crashes or Crashes(set())
        self.in_t, self.out_t, self.late_t, self.state_t, self.group, self.pid = (
            in_topic,
            out_topic,
            late_topic,
            state_topic,
            group,
            pid,
        )
        self.step = 0
        self._start()

    # --- lifecycle -----------------------------------------------------------------------------------
    def _start(self) -> None:
        self.b.init_transactions(self.pid)  # fence the crashed predecessor
        self.offset = self.b.committed(self.group, self.in_t, 0)
        self.state, self.max_ts = {}, -1
        if self.mode == "exactly_once":  # restore the snapshot committed together with the offset
            snaps = self.b.fetch(self.state_t, 0, 0, 10**9, read_committed=True)
            if snaps:
                snap = snaps[-1].value
                self.state = {tuple(json.loads(k)): v for k, v in snap["state"].items()}
                self.max_ts = snap["max_ts"]
        self.seq = {self.out_t: 0, self.late_t: 0}

    def watermark(self) -> int:
        return self.max_ts - LATENESS

    def _process(self, recs) -> tuple[list[dict], list[dict]]:
        out, late = [], []
        for r in recs:
            ts, key = r.ts, r.key
            start = ts - ts % WINDOW
            if start + WINDOW <= self.watermark():
                late.append({"key": key, "ts": ts, "value": r.value["v"]})
                continue
            cell = self.state.setdefault((key, start), [0, 0])
            cell[0] += 1
            cell[1] += r.value["v"]
            self.max_ts = max(self.max_ts, ts)
        for key, start in sorted(k for k in self.state if k[1] + WINDOW <= self.watermark()):
            c = self.state.pop((key, start))
            out.append({"key": key, "window_start": start, "count": c[0], "sum": c[1]})
        return out, late

    # --- one iteration -----------------------------------------------------------------------------------
    def run_once(self) -> bool:
        """Process one batch. Returns False when there is no more input."""
        recs = self.b.fetch(self.in_t, 0, self.offset, BATCH)
        if not recs:
            return False
        self.step += 1
        nxt = recs[-1].offset + 1
        if self.mode == "at_most_once":
            self.b.commit_offset(
                self.group, self.in_t, 0, nxt
            )  # commit first: a crash afterwards loses the batch
            self.crashes.hit(self.step, "after_commit_offset")
            out, late = self._process(recs)
            self._emit(out, late)
        elif self.mode == "at_least_once":
            out, late = self._process(recs)
            self._emit(out, late)
            self.crashes.hit(
                self.step, "after_output"
            )  # output is out, offset not yet committed -> replay duplicates it
            self.b.commit_offset(self.group, self.in_t, 0, nxt)
        else:
            self.b.begin(self.pid)
            out, late = self._process(recs)
            self._emit(out, late, txn=self.pid)
            snap = {
                "state": {json.dumps(list(k)): v for k, v in self.state.items()},
                "max_ts": self.max_ts,
            }
            self.b.produce(self.state_t, 0, "snapshot", snap, self.max_ts, txn=self.pid)
            self.crashes.hit(
                self.step, "after_output"
            )  # records pending in an open transaction: invisible to read_committed readers
            self.b.send_offsets(self.pid, self.group, self.in_t, 0, nxt)
            self.crashes.hit(self.step, "before_commit")
            self.b.commit(self.pid)
        self.offset = nxt
        self.crashes.hit(self.step, "after_batch")
        return True

    def _emit(self, out, late, txn: str | None = None) -> None:
        for o in out:
            self.b.produce(self.out_t, 0, o["key"], o, o["window_start"], txn=txn)
        for lt in late:
            self.b.produce(self.late_t, 0, lt["key"], lt, lt["ts"], txn=txn)

    def flush(self) -> None:
        """End of stream: close every remaining window (the watermark advances to infinity)."""
        self.max_ts += 10**9
        out, late = self._process([])
        if self.mode == "exactly_once":
            self.b.begin(self.pid)
            self._emit(out, late, txn=self.pid)
            self.b.produce(
                self.state_t,
                0,
                "snapshot",
                {"state": {}, "max_ts": self.max_ts},
                self.max_ts,
                txn=self.pid,
            )
            self.b.commit(self.pid)
        else:
            self._emit(out, late)


def run_to_completion(
    broker: Broker, mode: str, plan: set[tuple[int, str]] = frozenset(), max_restarts: int = 50
) -> int:
    """Drive the processor; after each injected crash a NEW processor instance starts from broker state only. Returns restarts."""
    crashes, restarts = Crashes(set(plan)), 0
    proc = Processor(broker, mode, crashes)
    while True:
        try:
            while proc.run_once():
                pass
            proc.flush()
            return restarts
        except Crash:
            restarts += 1
            if restarts > max_restarts:
                raise
            step = proc.step
            proc = Processor(
                broker, mode, crashes
            )  # fresh process: only broker-side state survives
            proc.step = step


def reference(events: list[tuple[str, int, int]]) -> tuple[dict, list]:
    """Independent sequential reference: windows emitted by a dict-based simulation with the same lateness rule, no batching, no broker."""
    totals: dict[tuple[str, int], list[int]] = {}
    late, max_ts = [], -1
    for key, ts, v in events:
        start = ts - ts % WINDOW
        if start + WINDOW <= max_ts - LATENESS:
            late.append((key, ts, v))
            continue
        c = totals.setdefault((key, start), [0, 0])
        c[0] += 1
        c[1] += v
        max_ts = max(max_ts, ts)
    return {k: tuple(v) for k, v in totals.items()}, late
