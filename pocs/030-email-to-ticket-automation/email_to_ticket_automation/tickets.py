"""Mail -> Ticket, routing, threading and idempotence. Redacts card/SSN numbers from summaries."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agent_policy_guardrails.detectors import DETECTORS

from .classify import Category, Priority, Ticket, classify
from .parse import Mail

CONFIG = json.loads(
    (Path(__file__).resolve().parent.parent / "config" / "routing.json").read_text()
)


def redact(text: str) -> str:
    spans = sorted(
        (s, e) for name in ("credit_card", "us_ssn", "iban") for s, e, _ in DETECTORS[name](text)
    )
    out, last = [], 0
    for s, e in spans:
        if s >= last:
            out.append(text[last:s] + "[REDACTED]")
            last = e
    return "".join(out) + text[last:]


@dataclass
class Store:
    tickets: dict[str, dict] = field(default_factory=dict)  # ticket id -> ticket record
    by_message: dict[str, str] = field(default_factory=dict)  # message id -> ticket id
    skipped: list[dict] = field(default_factory=list)

    def thread_ticket(self, mail: Mail) -> str | None:
        for ref in [mail.in_reply_to, *reversed(mail.references)]:
            if ref and ref in self.by_message:
                return self.by_message[ref]
        return None


def route(category: Category, priority: Priority, domain: str, confidence: float) -> dict:
    cfg = CONFIG["queues"]
    cat = category.value if confidence >= CONFIG["min_confidence"] else "unknown"
    q = cfg[cat]
    sla = q["sla_hours"] * (CONFIG["urgent_sla_factor"] if priority == Priority.urgent else 1)
    esc = domain in CONFIG["vip_domains"]
    return {
        "queue": q["queue"],
        "sla_hours": sla,
        "escalated": esc,
        "priority": Priority.urgent.value
        if esc and priority == Priority.normal
        else priority.value,
    }


def ingest(store: Store, mail: Mail) -> dict:
    """Return {'action': 'created'|'appended'|'duplicate'|'skipped', ...}. Never raises for hostile content."""
    if mail.message_id in store.by_message:
        return {"action": "duplicate", "ticket": store.by_message[mail.message_id]}
    if "auto_generated" in mail.flags:
        store.skipped.append({"message_id": mail.message_id, "reason": "auto_generated"})
        return {"action": "skipped", "reason": "auto_generated"}
    parent = store.thread_ticket(mail)
    if parent:
        store.by_message[mail.message_id] = parent
        store.tickets[parent]["messages"] += 1
        return {"action": "appended", "ticket": parent}
    cat, conf, pri, oid = classify(mail.subject, mail.body)
    t = Ticket(
        message_id=mail.message_id,
        category=cat,
        priority=pri,
        confidence=conf,
        order_id=oid,
        summary=redact(mail.body)[:200],
        flags=mail.flags,
    )
    r = route(t.category, t.priority, mail.domain, t.confidence)
    tid = f"T-{len(store.tickets) + 1:05d}"
    store.tickets[tid] = {
        **t.model_dump(mode="json"),
        **r,
        "customer": mail.sender,
        "messages": 1,
        "attachments": mail.attachments,
        "needs_human": bool(set(mail.flags) & {"suspicious_instruction", "risky_attachment"})
        or conf < CONFIG["min_confidence"],
    }
    store.by_message[mail.message_id] = tid
    return {"action": "created", "ticket": tid}
