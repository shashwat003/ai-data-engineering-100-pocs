"""Lineage store fed by OpenLineage RunEvents: validation, run tracking, dataset/column graph, impact analysis."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime

EVENT_TYPES = {"START", "RUNNING", "COMPLETE", "ABORT", "FAIL", "OTHER"}
TERMINAL = {"COMPLETE", "ABORT", "FAIL"}


class InvalidEvent(ValueError):
    pass


def _ds(items, where):
    out = []
    for d in items or []:
        if not isinstance(d, dict) or not d.get("namespace") or not d.get("name"):
            raise InvalidEvent(f"{where}: dataset needs namespace and name")
        out.append(d)
    return out


def validate(ev: dict) -> None:
    if not isinstance(ev, dict):
        raise InvalidEvent("event must be an object")
    for k in ("eventTime", "producer", "schemaURL"):
        if not isinstance(ev.get(k), str) or not ev[k]:
            raise InvalidEvent(f"missing {k}")
    try:
        datetime.fromisoformat(ev["eventTime"].replace("Z", "+00:00"))
    except ValueError as e:
        raise InvalidEvent("eventTime is not ISO-8601") from e
    if ev.get("eventType") not in EVENT_TYPES:
        raise InvalidEvent(f"eventType must be one of {sorted(EVENT_TYPES)}")
    try:
        uuid.UUID(str((ev.get("run") or {}).get("runId")))
    except ValueError as e:
        raise InvalidEvent("run.runId must be a UUID") from e
    job = ev.get("job") or {}
    if not job.get("namespace") or not job.get("name"):
        raise InvalidEvent("job needs namespace and name")
    _ds(ev.get("inputs"), "inputs")
    _ds(ev.get("outputs"), "outputs")


def key(d: dict) -> str:
    return f"{d['namespace']}:{d['name']}"


class LineageStore:
    def __init__(self):
        self.runs: dict[str, dict] = {}
        self.job_inputs: dict[str, set[str]] = defaultdict(set)
        self.job_outputs: dict[str, set[str]] = defaultdict(set)
        self.col_edges: dict[str, set[str]] = defaultdict(set)  # "ds.col" -> upstream "ds.col"
        self.seen: set[tuple[str, str]] = set()
        self.rejected = 0

    def ingest(self, ev: dict) -> bool:
        """Returns False for an exact duplicate (idempotent delivery)."""
        validate(ev)
        run_id, typ = ev["run"]["runId"], ev["eventType"]
        if (run_id, typ + ev["eventTime"]) in self.seen:
            return False
        self.seen.add((run_id, typ + ev["eventTime"]))
        job = f"{ev['job']['namespace']}:{ev['job']['name']}"
        r = self.runs.setdefault(run_id, {"job": job, "state": None, "start": None, "end": None})
        if r["job"] != job:
            raise InvalidEvent("runId already used by another job")
        if r["state"] in TERMINAL and typ not in TERMINAL:
            return True  # late non-terminal event: ignore, never resurrect a finished run
        if typ == "START":
            r["start"] = ev["eventTime"]
        if typ in TERMINAL:
            r["end"] = ev["eventTime"]
        if r["state"] not in TERMINAL or typ in TERMINAL:
            r["state"] = typ
        if typ == "COMPLETE":
            for d in _ds(ev.get("inputs"), "inputs"):
                self.job_inputs[job].add(key(d))
            for d in _ds(ev.get("outputs"), "outputs"):
                self.job_outputs[job].add(key(d))
                cl = (d.get("facets") or {}).get("columnLineage", {}).get("fields", {})
                for col, spec in cl.items():
                    for f in spec.get("inputFields", []):
                        self.col_edges[f"{key(d)}.{col}"].add(
                            f"{f['namespace']}:{f['name']}.{f['field']}"
                        )
        return True

    # --- queries ---------------------------------------------------------------------------------
    def _producers(self, ds: str) -> list[str]:
        return [j for j, outs in self.job_outputs.items() if ds in outs]

    def upstream(self, ds: str) -> set[str]:
        out, stack = set(), [ds]
        while stack:
            for j in self._producers(stack.pop()):
                for i in self.job_inputs[j]:
                    if i not in out and i != ds:
                        out.add(i)
                        stack.append(i)
        return out

    def downstream(self, ds: str) -> set[str]:
        out, stack = set(), [ds]
        while stack:
            cur = stack.pop()
            for j, ins in self.job_inputs.items():
                if cur in ins:
                    for o in self.job_outputs[j]:
                        if o not in out and o != ds:
                            out.add(o)
                            stack.append(o)
        return out

    def column_upstream(self, col: str) -> set[str]:
        out, stack = set(), [col]
        while stack:
            for u in self.col_edges.get(stack.pop(), ()):
                if u not in out:
                    out.add(u)
                    stack.append(u)
        return out

    def sources(self) -> set[str]:
        all_in = set().union(*self.job_inputs.values()) if self.job_inputs else set()
        produced = set().union(*self.job_outputs.values()) if self.job_outputs else set()
        return all_in - produced

    def stuck_runs(self) -> list[str]:
        """Runs that started but never reached a terminal state."""
        return sorted(r["job"] for r in self.runs.values() if r["state"] not in TERMINAL)

    def stale_datasets(self) -> dict[str, str]:
        """Datasets downstream of a job whose latest run failed, mapped to the failing job."""
        latest: dict[str, dict] = {}
        for r in self.runs.values():
            if r["job"] not in latest or (r["start"] or "") >= (latest[r["job"]]["start"] or ""):
                latest[r["job"]] = r
        stale: dict[str, str] = {}
        for job, r in latest.items():
            if r["state"] in ("FAIL", "ABORT"):
                for o in self.job_outputs.get(job, ()):
                    stale.setdefault(o, job)
                    for d in self.downstream(o):
                        stale.setdefault(d, job)
        return stale
