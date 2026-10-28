"""Durable human-approval workflow: quorum, separation of duties, SLA escalation, signed single-use tokens.

State machine (every transition is a compare-and-swap in SQLite, so concurrent decisions cannot execute twice):

  pending --quorum met--> approved --execute--> executed | failed
  pending --any reject--> rejected
  pending --sla--> escalated --(second sla)--> expired        escalated --quorum met--> approved
  pending|escalated --cancel--> cancelled
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass

from incident_triage_agent.audit import AuditLog

TRANSITIONS = {
    "pending": {"approved", "rejected", "escalated", "cancelled"},
    "escalated": {"approved", "rejected", "expired", "cancelled"},
    "approved": {"executed", "failed"},
    "rejected": set(), "expired": set(), "cancelled": set(), "executed": set(), "failed": set(),
}  # fmt: skip
TERMINAL = {s for s, nxt in TRANSITIONS.items() if not nxt}


class WorkflowError(Exception):
    pass


@dataclass(frozen=True)
class Policy:
    action: str
    required: int  # approvals needed
    approvers: frozenset[str]  # first-line approvers
    escalation: frozenset[str]  # added after the first SLA
    sla_s: float = 3600


class Workflow:
    def __init__(self, db: sqlite3.Connection, policies: dict[str, Policy], executor: Callable[[str, dict], None], *, secret: bytes,
                 clock: Callable[[], float], token_ttl_s: float = 86_400):  # fmt: skip
        self.db, self.policies, self.executor, self.secret, self.clock, self.token_ttl = (
            db,
            policies,
            executor,
            secret,
            clock,
            token_ttl_s,
        )
        self.lock = threading.Lock()
        db.executescript("""
        CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT, payload TEXT, requester TEXT,
            state TEXT NOT NULL, created REAL, deadline REAL, escalated INTEGER DEFAULT 0, result TEXT);
        CREATE TABLE IF NOT EXISTS decisions (request_id INTEGER, approver TEXT, verdict TEXT, ts REAL, PRIMARY KEY (request_id, approver));
        CREATE TABLE IF NOT EXISTS used_tokens (nonce TEXT PRIMARY KEY);""")
        self.audit = AuditLog(db, lambda: f"t={self.clock():.0f}")
        self.outbox: list[
            dict
        ] = []  # notifications that would be sent (chat/email); recorded, not sent anywhere

    # ---- helpers
    def _state(self, rid: int) -> str:
        row = self.db.execute("SELECT state FROM requests WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise WorkflowError(f"unknown request {rid}")
        return row[0]

    def _move(self, rid: int, new: str, expect: str | None = None) -> None:
        """Compare-and-swap transition; refuses illegal moves and stale expectations."""
        cur = self._state(rid)
        if (expect and cur != expect) or new not in TRANSITIONS[cur]:
            raise WorkflowError(f"illegal transition {cur} -> {new}")
        n = self.db.execute(
            "UPDATE requests SET state=? WHERE id=? AND state=?", (new, rid, cur)
        ).rowcount
        if n != 1:
            raise WorkflowError("concurrent update lost the race")
        self.audit.append(str(rid), "state", {"from": cur, "to": new})

    def _eligible(self, rid: int) -> frozenset[str]:
        action, esc = self.db.execute(
            "SELECT action, escalated FROM requests WHERE id=?", (rid,)
        ).fetchone()
        p = self.policies[action]
        return p.approvers | (p.escalation if esc else frozenset())

    # ---- tokens: HMAC-signed, expiring, single use, bound to (request, approver)
    def issue_token(self, rid: int, approver: str) -> str:
        body = json.dumps(
            {
                "r": rid,
                "a": approver,
                "exp": self.clock() + self.token_ttl,
                "n": secrets.token_hex(8),
            },
            sort_keys=True,
        )
        sig = hmac.new(self.secret, body.encode(), hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(f"{body}|{sig}".encode()).decode()

    def _verify(self, token: str) -> dict:
        try:
            body, sig = base64.urlsafe_b64decode(token.encode()).decode().rsplit("|", 1)
        except Exception as e:
            raise WorkflowError("malformed token") from e
        if not hmac.compare_digest(
            sig, hmac.new(self.secret, body.encode(), hashlib.sha256).hexdigest()
        ):
            raise WorkflowError("bad token signature")
        claims = json.loads(body)
        if claims["exp"] < self.clock():
            raise WorkflowError("token expired")
        return claims

    # ---- API
    def submit(self, action: str, payload: dict, requester: str) -> int:
        if action not in self.policies:
            raise WorkflowError(f"no policy for action {action!r}")
        p = self.policies[action]
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO requests (action, payload, requester, state, created, deadline) VALUES (?,?,?,?,?,?)",
                                  (action, json.dumps(payload, sort_keys=True), requester, "pending", self.clock(), self.clock() + p.sla_s))  # fmt: skip
            rid = cur.lastrowid
            self.audit.append(str(rid), "submitted", {"action": action, "requester": requester})
            for a in sorted(p.approvers - {requester}):
                self.outbox.append(
                    {
                        "to": a,
                        "request": rid,
                        "token": self.issue_token(rid, a),
                        "kind": "approval_needed",
                    }
                )
        return rid

    def decide(self, token: str, verdict: str) -> str:
        if verdict not in ("approve", "reject"):
            raise WorkflowError("verdict must be approve or reject")
        claims = self._verify(token)
        rid, who = claims["r"], claims["a"]
        with self.lock, self.db:
            if (
                self.db.execute(
                    "INSERT OR IGNORE INTO used_tokens VALUES (?)", (claims["n"],)
                ).rowcount
                == 0
            ):
                raise WorkflowError("token already used")
            state = self._state(rid)
            if state in TERMINAL or state == "approved":
                raise WorkflowError(f"request is already {state}")
            requester = self.db.execute(
                "SELECT requester FROM requests WHERE id=?", (rid,)
            ).fetchone()[0]
            if who == requester:
                raise WorkflowError(
                    "separation of duties: requester cannot decide their own request"
                )
            if who not in self._eligible(rid):
                raise WorkflowError(f"{who} is not an eligible approver")
            self.db.execute(
                "INSERT OR REPLACE INTO decisions VALUES (?,?,?,?)",
                (rid, who, verdict, self.clock()),
            )
            self.audit.append(str(rid), "decision", {"approver": who, "verdict": verdict})
            if verdict == "reject":
                self._move(rid, "rejected")
                return "rejected"
            approvals = self.db.execute(
                "SELECT COUNT(*) FROM decisions WHERE request_id=? AND verdict='approve'", (rid,)
            ).fetchone()[0]
            if (
                approvals
                >= self.policies[
                    self.db.execute("SELECT action FROM requests WHERE id=?", (rid,)).fetchone()[0]
                ].required
            ):
                self._move(rid, "approved")
                return self._execute(rid)
            return state

    def _execute(self, rid: int) -> str:
        action, payload = self.db.execute(
            "SELECT action, payload FROM requests WHERE id=?", (rid,)
        ).fetchone()
        try:
            self.executor(action, json.loads(payload))
            self._move(rid, "executed")
            return "executed"
        except Exception as e:
            self.db.execute("UPDATE requests SET result=? WHERE id=?", (type(e).__name__, rid))
            self._move(rid, "failed")
            return "failed"

    def tick(self) -> list[tuple[int, str]]:
        """Apply SLA timers: first miss escalates (widening the approver set), second miss expires."""
        moved = []
        with self.lock, self.db:
            now = self.clock()
            for rid, state, _esc in self.db.execute(
                "SELECT id, state, escalated FROM requests WHERE state IN ('pending','escalated') AND deadline <= ?",
                (now,),
            ).fetchall():
                p = self.policies[
                    self.db.execute("SELECT action FROM requests WHERE id=?", (rid,)).fetchone()[0]
                ]
                if state == "pending":
                    self._move(rid, "escalated")
                    self.db.execute(
                        "UPDATE requests SET escalated=1, deadline=? WHERE id=?",
                        (now + p.sla_s, rid),
                    )
                    for a in sorted(p.escalation):
                        self.outbox.append(
                            {
                                "to": a,
                                "request": rid,
                                "token": self.issue_token(rid, a),
                                "kind": "escalated",
                            }
                        )
                    moved.append((rid, "escalated"))
                else:
                    self._move(rid, "expired")
                    moved.append((rid, "expired"))
        return moved

    def cancel(self, rid: int, by: str) -> None:
        with self.lock, self.db:
            if (
                by
                != self.db.execute("SELECT requester FROM requests WHERE id=?", (rid,)).fetchone()[
                    0
                ]
            ):
                raise WorkflowError("only the requester can cancel")
            self._move(rid, "cancelled")

    def status(self, rid: int) -> dict:
        r = self.db.execute(
            "SELECT id, action, requester, state, escalated FROM requests WHERE id=?", (rid,)
        ).fetchone()
        if r is None:
            raise WorkflowError(f"unknown request {rid}")
        decisions = self.db.execute(
            "SELECT approver, verdict FROM decisions WHERE request_id=? ORDER BY ts, approver",
            (rid,),
        ).fetchall()
        return {
            "id": r[0],
            "action": r[1],
            "requester": r[2],
            "state": r[3],
            "escalated": bool(r[4]),
            "decisions": decisions,
        }
