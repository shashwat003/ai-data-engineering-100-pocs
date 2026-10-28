"""Human approval records: bound to the exact arguments, single-use, expiring, never self-approved."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


class ApprovalError(Exception):
    pass


def args_hash(tool: str, args: dict) -> str:
    return hashlib.sha256(json.dumps([tool, args], sort_keys=True).encode()).hexdigest()


@dataclass
class ApprovalRequest:
    id: int
    run_id: str
    tool: str
    args: dict
    reason: str


class Approver:
    """Interface: return (approve, approver_id, reason), or None to leave the request pending."""

    def decide(self, request: ApprovalRequest) -> tuple[bool, str, str] | None:
        raise NotImplementedError


class QueueApprover(Approver):
    """Never decides: requests wait in the database for a human (`ApprovalStore.decide`)."""

    def decide(self, request: ApprovalRequest) -> None:
        return None


class ScriptedApprover(Approver):
    """For tests and the demo: approves tools listed in `approve`, denies the rest."""

    def __init__(self, approve: set[str], approver_id: str = "oncall.alice"):
        self.approve, self.approver_id = approve, approver_id

    def decide(self, request: ApprovalRequest) -> tuple[bool, str, str]:
        ok = request.tool in self.approve
        return ok, self.approver_id, "scripted approval" if ok else "scripted denial"


class ApprovalStore:
    def __init__(
        self,
        con: sqlite3.Connection,
        *,
        allowed_approvers: set[str],
        ttl_minutes: int = 60,
        now: Callable[[], datetime] | None = None,
    ):
        self.con, self.allowed, self.ttl = con, allowed_approvers, timedelta(minutes=ttl_minutes)
        self._now = now or (lambda: datetime.now(UTC).replace(tzinfo=None, microsecond=0))
        con.execute("""CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, tool TEXT, args TEXT, args_hash TEXT,
            reason TEXT, status TEXT, requested_at TEXT, expires_at TEXT, approver TEXT,
            decided_at TEXT, decision_reason TEXT)""")

    def request(self, run_id: str, tool: str, args: dict, reason: str) -> ApprovalRequest:
        now = self._now()
        cur = self.con.execute(
            "INSERT INTO approvals (run_id, tool, args, args_hash, reason, status, requested_at,"
            " expires_at) VALUES (?,?,?,?,?,'pending',?,?)",
            (run_id, tool, json.dumps(args, sort_keys=True), args_hash(tool, args), reason,
             now.isoformat(), (now + self.ttl).isoformat()),
        )  # fmt: skip
        self.con.commit()
        return ApprovalRequest(cur.lastrowid, run_id, tool, args, reason)

    def get(self, approval_id: int) -> dict:
        cur = self.con.execute("SELECT * FROM approvals WHERE id=?", (approval_id,))
        row = cur.fetchone()
        if row is None:
            raise ApprovalError(f"no approval {approval_id}")
        return dict(zip([c[0] for c in cur.description], row, strict=True))

    def decide(self, approval_id: int, approver: str, approve: bool, reason: str = "") -> None:
        rec = self.get(approval_id)
        if approver not in self.allowed:
            raise ApprovalError(f"{approver!r} is not an authorised approver")
        if rec["status"] != "pending":
            raise ApprovalError(f"approval {approval_id} is already {rec['status']}")
        self.con.execute(
            "UPDATE approvals SET status=?, approver=?, decided_at=?, decision_reason=? WHERE id=?",
            ("approved" if approve else "denied", approver, self._now().isoformat(), reason,
             approval_id),
        )  # fmt: skip
        self.con.commit()

    def consume(self, approval_id: int, tool: str, args: dict) -> None:
        """Validate and use up an approval at execution time (checked again, not trusted)."""
        rec = self.get(approval_id)
        if rec["status"] != "approved":
            raise ApprovalError(f"approval {approval_id} is {rec['status']}, not approved")
        if datetime.fromisoformat(rec["expires_at"]) < self._now():
            self.con.execute("UPDATE approvals SET status='expired' WHERE id=?", (approval_id,))
            self.con.commit()
            raise ApprovalError(f"approval {approval_id} expired")
        if rec["args_hash"] != args_hash(tool, args):
            raise ApprovalError(f"approval {approval_id} does not cover these arguments")
        self.con.execute("UPDATE approvals SET status='consumed' WHERE id=?", (approval_id,))
        self.con.commit()
