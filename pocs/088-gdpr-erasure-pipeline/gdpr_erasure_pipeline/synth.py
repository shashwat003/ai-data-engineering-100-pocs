"""Synthetic customers, events and support tickets. Fictional people on example.com."""

from __future__ import annotations

import random
from datetime import date, timedelta

FIRST = ["Ana", "Ben", "Chen", "Dara", "Eli", "Fay", "Gus", "Hana", "Ivan", "Jia"]
LAST = ["Adams", "Brown", "Clark", "Diaz", "Evans", "Frost", "Garcia", "Hill"]
ISSUES = [
    "the invoice shows a duplicate charge",
    "I cannot log in after the update",
    "my delivery never arrived",
    "please change my plan to monthly",
    "the export button returns an error",
]
TODAY = date(2025, 6, 1)


def people(n: int = 30, seed: int = 8) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        out.append(
            {
                "user_id": f"u{i:03d}",
                "name": name,
                "email": f"{name.lower().replace(' ', '.')}.{i}@example.com",
            }
        )
    return out


def events(ppl: list[dict], n: int = 600, seed: int = 9) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        p = rng.choice(ppl)
        rows.append({"event_id": f"e{i:05d}", "user_id": p["user_id"], "email": p["email"], "event_type": rng.choice(["view", "cart", "purchase"]),
                     "amount": round(rng.uniform(0, 120), 2), "event_date": (TODAY - timedelta(days=rng.randint(0, 400))).isoformat()})  # fmt: skip
    return rows


def tickets(ppl: list[dict], per_person: int = 2, seed: int = 10) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for p in ppl:
        for k in range(per_person):
            out.append(
                {
                    "ticket_id": f"{p['user_id']}-t{k}",
                    "subject": p["user_id"],
                    "text": f"{p['name']} ({p['email']}) writes: {rng.choice(ISSUES)}.",
                }
            )
    return out
