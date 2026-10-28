"""Rule-based category / priority / order-id extraction (a transparent baseline).

An LLM could be plugged into `classify()` for low-confidence mails; its output would be validated by
`Ticket` like everything else. No LLM is used or measured here.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field


class Category(str, Enum):  # noqa: UP042
    billing = "billing"
    shipping = "shipping"
    technical = "technical"
    account = "account"
    feedback = "feedback"
    unknown = "unknown"


class Priority(str, Enum):  # noqa: UP042
    urgent = "urgent"
    high = "high"
    normal = "normal"
    low = "low"


KEYWORDS = {
    Category.billing: ["charged", "refund", "invoice", "billing", "payment", "receipt", "card"],
    Category.shipping: [
        "package",
        "delivery",
        "parcel",
        "tracking",
        "shipping",
        "arrived",
        "damaged",
        "replacement",
    ],
    Category.technical: [
        "crash",
        "error",
        "broken",
        "500",
        "bug",
        "login after",
        "export",
        "blocking",
    ],
    Category.account: ["password", "log in", "account", "delete my", "email address"],
    Category.feedback: [
        "suggestion",
        "feature request",
        "would be nice",
        "thanks",
        "great product",
        "lovely",
    ],
}
URGENT = re.compile(
    r"(?i)\burgent\b|\basap\b|production is down|cannot work at all|customers are affected"
)
ORDER = re.compile(r"\bORD-\d{4,8}\b")


class Ticket(BaseModel):
    message_id: str
    category: Category
    priority: Priority
    confidence: float = Field(ge=0, le=1)
    order_id: str | None = Field(default=None, pattern=r"^ORD-\d{4,8}$")
    summary: str = Field(max_length=200)
    flags: list[str] = []


def classify(subject: str, body: str) -> tuple[Category, float, Priority, str | None]:
    text = f"{subject} {body}".lower()
    scores = {c: sum(text.count(k) for k in kws) for c, kws in KEYWORDS.items()}
    top = sorted(scores.items(), key=lambda kv: -kv[1])
    best, n = top[0]
    total = sum(scores.values())
    conf = 0.0 if total == 0 else round(n / total * min(1.0, 0.5 + 0.25 * n), 2)
    if total == 0 or (len(top) > 1 and top[1][1] == n):
        conf = min(conf, 0.4)
    priority = Priority.urgent if URGENT.search(body) else Priority.normal
    m = ORDER.search(f"{subject} {body}")
    return (best if total else Category.unknown), conf, priority, m.group() if m else None
