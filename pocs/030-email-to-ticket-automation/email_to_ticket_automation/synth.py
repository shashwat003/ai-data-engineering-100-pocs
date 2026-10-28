"""Synthetic raw emails (RFC 5322 bytes) with labels. Domains are reserved example.* names."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime

NAMES = ["Ana Ng", "Ben Ito", "Chen Wu", "Dara Roy", "Eli Fox", "Fay Lee", "Gus Hall"]
DOMAINS = ["mail.example", "corp.example", "bigcorp.example", "shop.example"]
BODIES = {
    "billing": [
        "I was charged twice for order {oid}. Please refund the duplicate payment.",
        "My invoice for order {oid} shows the wrong amount, can you correct the billing?",
        "Need a receipt and refund for {oid}, the card payment failed but money left my account.",
    ],
    "shipping": [
        "My package for order {oid} has not arrived and the tracking has not updated for a week.",
        "Delivery of {oid} is late, where is my parcel?",
        "Order {oid} arrived damaged during shipping, I want a replacement.",
    ],
    "technical": [
        "The app crashes with an error when I open the dashboard (order {oid}).",
        "I get a 500 error on login after the update, this is blocking our team.",
        "The export feature is broken and returns an error for order {oid}.",
    ],
    "account": [
        "Please reset my password, I cannot log in to my account.",
        "I want to change the email address on my account and close the old one.",
        "How do I delete my account and personal data?",
    ],
    "feedback": [
        "Great product, but it would be nice to have dark mode.",
        "Just wanted to say thanks, the new report page is lovely. A suggestion: add CSV export.",
        "Feature request: schedule reports weekly.",
    ],
}
URGENT = [
    "This is urgent, production is down.",
    "URGENT: we cannot work at all.",
    "Asap please, customers are affected.",
]


def make_email(
    rng: random.Random, i: int, category: str, kind: str = "plain"
) -> tuple[bytes, dict]:
    name, dom = rng.choice(NAMES), rng.choice(DOMAINS)
    sender = f"{name.split()[0].lower()}{i}@{dom}"
    oid = f"ORD-{rng.randint(10000, 99999)}"
    urgent = rng.random() < 0.2
    body = rng.choice(BODIES[category]).format(oid=oid) + (
        " " + rng.choice(URGENT) if urgent else ""
    )
    m = EmailMessage()
    m["From"] = f"{name} <{sender}>"
    m["To"] = "support@company.example"
    m["Subject"] = rng.choice(
        ["Help needed", "Question", "Problem with my order", "Issue", "Support request"]
    )
    m["Message-ID"] = f"<msg{i}@{dom}>"
    m["Date"] = format_datetime(datetime(2025, 3, 1, tzinfo=UTC) + timedelta(minutes=i * 7))
    if kind == "html":  # html-only mail with a script and a tracking pixel
        m.set_content(
            f"<p>{body}</p><script>steal()</script><img src='http://tracker.example/x.png'>",
            subtype="html",
        )
    else:
        m.set_content(body)
    if kind == "attachment":
        m.add_attachment(
            b"MZ\x90\x00fake-binary",
            maintype="application",
            subtype="octet-stream",
            filename="invoice.pdf.exe",
        )
    label = {
        "category": category,
        "priority": "urgent" if urgent else "normal",
        "order_id": oid if "{oid}" in " ".join(BODIES[category]) and oid in body else None,
        "sender": sender,
        "vip": dom == "bigcorp.example",
    }
    return m.as_bytes(), label


def corpus(n: int = 150, seed: int = 12) -> list[tuple[bytes, dict]]:
    rng = random.Random(seed)
    cats = list(BODIES)
    out = []
    for i in range(n):
        kind = rng.choices(["plain", "html", "attachment"], [0.7, 0.2, 0.1])[0]
        out.append(make_email(rng, i, cats[i % len(cats)], kind))
    return out


# Hand-written emails WITHOUT the template vocabulary: paraphrases, mixed signals, terse, quoted threads, non-English.
HARD = [
    (
        "Where is my stuff",
        "Ordered two weeks ago and nothing has come through the door yet.",
        "shipping",
    ),
    ("money", "You took the money from me two times for the same thing.", "billing"),
    ("can't get in", "The site just spins when I try to sign in with my usual details.", "account"),
    ("hi", "Love the new layout! Could you add a night theme someday?", "feedback"),
    (
        "Re: Re: your reply",
        "> We have escalated your case.\n> Regards, Support\n\nStill waiting for my money back, nobody has answered.",
        "billing",
    ),
    ("Problem", "The thing froze and showed a red message, then closed by itself.", "technical"),
    ("Pedido", "No he recibido mi pedido y han pasado diez días.", "shipping"),
    ("Cobro duplicado", "Me cobraron dos veces la factura de este mes.", "billing"),
    ("question", "Is it possible to move my subscription to my colleague's address?", "account"),
    (
        "Thanks and a bug",
        "Thanks for the quick fix last week. Now the report page gives an error and the export is broken again.",
        "technical",
    ),
    ("Invoice and delivery", "My invoice is wrong and the parcel is also late.", "billing"),
    ("URGENT", "Everything is down!!!", "technical"),
    ("Not happy", "This is the third time I write. Your service is slow and rude.", "feedback"),
    ("Update", "Please update the name on my profile, it is misspelled.", "account"),
    (
        "Order stuck",
        "The courier says delivered but my neighbour did not see anything either.",
        "shipping",
    ),
]


def hard_emails() -> list[tuple[bytes, dict]]:
    out = []
    for i, (subject, body, cat) in enumerate(HARD):
        m = EmailMessage()
        m["From"] = f"Pat Doe <pat{i}@mail.example>"
        m["To"] = "support@company.example"
        m["Subject"] = subject
        m["Message-ID"] = f"<hard{i}@mail.example>"
        m.set_content(body)
        out.append((m.as_bytes(), {"category": cat}))
    return out
