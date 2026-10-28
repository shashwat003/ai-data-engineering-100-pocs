"""Diff-based reverse ETL: warehouse rows -> mapped CRM fields -> minimal batches, with retries and safe deletes."""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

import duckdb

MAPPING = {  # CRM field <- (warehouse column, transform). Only these columns ever leave the warehouse.
    "email": ("email", str.lower), "first_name": ("first_name", None), "last_name": ("last_name", None),
    "lifetime_value": ("lifetime_value", lambda v: round(float(v), 2)), "churn_risk": ("churn_risk", lambda v: round(float(v), 3)), "segment": ("segment", None),
}  # fmt: skip
BATCH = 100
MAX_ATTEMPTS = 6


class MassDeleteBlocked(RuntimeError):
    pass


class SyncAborted(RuntimeError):
    pass


def h(fields: dict) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16]


def read_source(con: duckdb.DuckDBPyConnection) -> dict[str, dict]:
    cols = ", ".join(c for c, _ in MAPPING.values())
    out = {}
    for row in con.execute(f"SELECT customer_id, {cols} FROM customers_360").fetchall():  # noqa: S608 (columns come from MAPPING)
        out[row[0]] = {
            crm: (fn(v) if fn and v is not None else v)
            for (crm, (_c, fn)), v in zip(MAPPING.items(), row[1:], strict=True)
        }
    return out


class State:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path)
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS synced (external_id TEXT PRIMARY KEY, fields_hash TEXT, fields_json TEXT, status TEXT); CREATE TABLE IF NOT EXISTS dead (external_id TEXT PRIMARY KEY, fields_hash TEXT, error TEXT);"
        )

    def rows(self) -> dict[str, tuple[str, dict, str]]:
        return {
            r[0]: (r[1], json.loads(r[2]), r[3])
            for r in self.db.execute(
                "SELECT external_id, fields_hash, fields_json, status FROM synced"
            )
        }

    def dead(self) -> dict[str, tuple[str, str]]:
        return {
            r[0]: (r[1], r[2])
            for r in self.db.execute("SELECT external_id, fields_hash, error FROM dead")
        }

    def put(self, ext: str, fields: dict, status: str = "active") -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO synced VALUES (?,?,?,?)",
                (ext, h(fields), json.dumps(fields, sort_keys=True), status),
            )
            self.db.execute("DELETE FROM dead WHERE external_id = ?", (ext,))

    def set_status(self, ext: str, status: str) -> None:
        with self.db:
            self.db.execute("UPDATE synced SET status = ? WHERE external_id = ?", (status, ext))

    def bury(self, ext: str, fields: dict, error: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO dead VALUES (?,?,?)", (ext, h(fields), error))


@dataclass
class Plan:
    create: dict[str, dict] = field(default_factory=dict)
    update: dict[str, dict] = field(default_factory=dict)  # only the changed fields
    archive: list[str] = field(default_factory=list)
    unchanged: int = 0
    skipped_dead: int = 0


def make_plan(source: dict[str, dict], state: State) -> Plan:
    cur, dead = state.rows(), state.dead()
    p = Plan()
    for ext, fields in source.items():
        if ext in dead and dead[ext][0] == h(fields):
            p.skipped_dead += 1
        elif ext not in cur or cur[ext][2] == "archived":
            p.create[ext] = fields
        elif cur[ext][0] != h(fields):
            changed = {k: v for k, v in fields.items() if cur[ext][1].get(k) != v}
            p.update[ext] = changed or fields
        else:
            p.unchanged += 1
    p.archive = sorted(e for e, (_h, _f, st) in cur.items() if st == "active" and e not in source)
    return p


@dataclass
class Report:
    created: int = 0
    updated: int = 0
    archived: int = 0
    unchanged_by_crm: int = 0
    dead_lettered: int = 0
    api_calls: int = 0
    retries: int = 0
    rate_limited: int = 0
    bytes_sent: int = 0
    errors: list[str] = field(default_factory=list)


class Syncer:
    def __init__(
        self,
        client,
        state: State,
        sleep: Callable[[float], None],
        seed: int = 0,
        max_delete_fraction: float = 0.10,
    ):
        self.client, self.state, self.sleep = client, state, sleep
        self.rng, self.max_delete_fraction = random.Random(seed), max_delete_fraction

    def _post(self, path: str, body: dict, rep: Report) -> dict:
        payload = json.dumps(body, sort_keys=True)
        key = hashlib.sha256(
            (path + payload).encode()
        ).hexdigest()  # same batch -> same key, so a retry cannot double-apply
        for attempt in range(MAX_ATTEMPTS):
            rep.api_calls += 1
            rep.bytes_sent += len(payload)
            r = self.client.post(
                path,
                content=payload,
                headers={"Idempotency-Key": key, "Content-Type": "application/json"},
            )
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                rep.rate_limited += 1
                rep.retries += 1
                self.sleep(float(r.headers.get("Retry-After", 1)))
            elif r.status_code >= 500:
                rep.retries += 1
                self.sleep(min(30.0, 2**attempt) * (0.5 + self.rng.random()))
            else:
                raise SyncAborted(f"{path} rejected the request ({r.status_code}): {r.text[:120]}")
        raise SyncAborted(f"{path} still failing after {MAX_ATTEMPTS} attempts")

    def run(
        self, source: dict[str, dict], dry_run: bool = False, allow_mass_delete: bool = False
    ) -> tuple[Plan, Report]:
        plan = make_plan(source, self.state)
        rep = Report()
        active = sum(1 for _h, _f, st in self.state.rows().values() if st == "active")
        if (
            plan.archive
            and not allow_mass_delete
            and active
            and len(plan.archive) / active > self.max_delete_fraction
        ):
            raise MassDeleteBlocked(
                f"{len(plan.archive)} of {active} contacts would be archived; refusing without allow_mass_delete"
            )
        if dry_run:
            return plan, rep
        todo = [(e, f, "create") for e, f in sorted(plan.create.items())] + [
            (e, f, "update") for e, f in sorted(plan.update.items())
        ]
        for i in range(0, len(todo), BATCH):
            chunk = todo[i : i + BATCH]
            res = self._post(
                "/v1/contacts/batch-upsert",
                {"records": [{"external_id": e, "fields": f} for e, f, _ in chunk]},
                rep,
            )
            by_id = {r["external_id"]: r for r in res["results"]}
            for e, _f, _kind in chunk:
                r = by_id.get(e, {"status": "error", "error": "missing from response"})
                if r["status"] == "error":
                    full = source[e]
                    self.state.bury(e, full, r.get("error", "error"))
                    rep.dead_lettered += 1
                    continue
                self.state.put(e, source[e])  # commit per record after the CRM confirmed it
                rep.created += r["status"] == "created"
                rep.updated += r["status"] == "updated"
                rep.unchanged_by_crm += r["status"] == "unchanged"
        for i in range(0, len(plan.archive), BATCH):
            ids = plan.archive[i : i + BATCH]
            self._post("/v1/contacts/batch-archive", {"external_ids": ids}, rep)
            for e in ids:
                self.state.set_status(e, "archived")
            rep.archived += len(ids)
        return plan, rep
