"""Erasure orchestration across stores: idempotent steps, verification, SLA, audit without personal data."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from deltalake import DeltaTable

from . import lake
from .vectors import TicketStore, file_residue

SLA_DAYS = 30


@dataclass
class Pipeline:
    state_dir: Path
    lake_path: Path
    tickets: TicketStore
    salt: bytes
    holds: set[str] = field(default_factory=set)

    def __post_init__(self):
        if len(self.salt) < 16:
            raise ValueError("salt must be at least 16 bytes")
        self.state_dir = Path(self.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.req_file, self.audit_file = (
            self.state_dir / "requests.json",
            self.state_dir / "audit.jsonl",
        )
        self.requests: dict[str, dict] = (
            json.loads(self.req_file.read_text()) if self.req_file.exists() else {}
        )

    def pseudonym(self, subject: str) -> str:
        return hmac.new(self.salt, subject.encode(), hashlib.sha256).hexdigest()[:16]

    def _save(self) -> None:
        tmp = self.req_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.requests, indent=1))
        tmp.replace(self.req_file)

    def _audit(self, event: dict) -> None:
        prev = "0" * 64
        if self.audit_file.exists() and self.audit_file.read_text().strip():
            prev = json.loads(self.audit_file.read_text().splitlines()[-1])["hash"]
        body = json.dumps(event, sort_keys=True)
        h = hashlib.sha256((prev + body).encode()).hexdigest()
        with self.audit_file.open("a") as fh:
            fh.write(json.dumps({**event, "prev": prev, "hash": h}) + "\n")

    def verify_audit_chain(self) -> bool:
        prev = "0" * 64
        for line in self.audit_file.read_text().splitlines():
            rec = json.loads(line)
            body = {k: v for k, v in rec.items() if k not in ("prev", "hash")}
            if (
                rec["prev"] != prev
                or rec["hash"]
                != hashlib.sha256((prev + json.dumps(body, sort_keys=True)).encode()).hexdigest()
            ):
                return False
            prev = rec["hash"]
        return True

    def erase(
        self,
        subject: str,
        today: date,
        identifiers: list[str] | None = None,
        fail_after_lake: bool = False,
    ) -> dict:
        """Erase one subject everywhere. Re-running resumes: completed steps are not repeated."""
        if subject in self.holds:
            rid = self.pseudonym(subject)
            self._audit({"request": rid, "event": "refused_legal_hold", "at": today.isoformat()})
            return {"status": "refused_legal_hold"}
        rid = self.pseudonym(subject)
        req = self.requests.setdefault(
            rid,
            {
                "received": today.isoformat(),
                "due": (today + timedelta(days=SLA_DAYS)).isoformat(),
                "steps": {},
                "status": "in_progress",
            },
        )
        if req["status"] == "completed":
            return {"status": "already_completed", "request": rid}
        self._audit({"request": rid, "event": "received", "at": today.isoformat()})
        if "lake" not in req["steps"]:
            req["steps"]["lake"] = lake.erase_subject(self.lake_path, subject)
            self._save()
        if fail_after_lake:
            raise RuntimeError("simulated crash after the lake step")
        if "tickets" not in req["steps"]:
            req["steps"]["tickets"] = self.tickets.erase_subject(subject)
            self._save()
        needles = [subject, *(identifiers or [])]
        left = lake.residue(self.lake_path, needles) + file_residue(self.tickets.dir, needles)
        tt = lake.time_travel_exposes(self.lake_path, subject)
        req["verification"] = {
            "residue_hits": len(left),
            "readable_old_versions": tt,
            "lake_rows_left": lake.count(self.lake_path, subject),
            "tickets_left": self.tickets.count(subject),
        }
        ok = (
            not left
            and not tt
            and req["verification"]["lake_rows_left"] == 0
            and req["verification"]["tickets_left"] == 0
        )
        req["status"] = "completed" if ok else "failed_verification"
        req["completed"] = today.isoformat()
        self._save()
        self._audit(
            {
                "request": rid,
                "event": req["status"],
                "at": today.isoformat(),
                "lake_rows": req["steps"]["lake"]["rows_deleted"],
                "tickets": req["steps"]["tickets"]["tickets_deleted"],
            }
        )
        return {
            "status": req["status"],
            "request": rid,
            **req["verification"],
            "steps": req["steps"],
        }

    def overdue(self, today: date) -> list[str]:
        return [
            r
            for r, v in self.requests.items()
            if v["status"] != "completed" and date.fromisoformat(v["due"]) < today
        ]


def apply_retention(lake_path: Path, today: date, days: int, holds: set[str]) -> dict:
    """Delete events older than `days` unless their subject is under legal hold, then vacuum."""
    cutoff = (today - timedelta(days=days)).isoformat()
    dt = DeltaTable(str(lake_path))
    pred = f"event_date < '{cutoff}'"
    if holds:
        if any(not h.replace("-", "").replace("_", "").isalnum() for h in holds):
            raise ValueError("unsafe subject id in holds")
        pred += " AND user_id NOT IN (" + ", ".join(f"'{h}'" for h in sorted(holds)) + ")"
    res = dt.delete(pred)
    files = len(
        DeltaTable(str(lake_path)).vacuum(
            retention_hours=0, enforce_retention_duration=False, dry_run=False
        )
    )
    return {"cutoff": cutoff, "rows_deleted": res["num_deleted_rows"], "files_vacuumed": files}
