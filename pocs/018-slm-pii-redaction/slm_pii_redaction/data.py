"""Synthetic text with labelled PII spans. Train and test use disjoint name pools so the tagger is
evaluated on names it has never seen. All values are fictional (reserved example domains, 555 numbers)."""

from __future__ import annotations

import random

from agent_policy_guardrails.data import iban_de, luhn_number

FIRST_TRAIN = "Ana Ben Chen Dara Eli Fay Gus Hana Ivan Jia Kai Lena Milo Nia Omar Pia Raj Sara Tom Uma".split()
FIRST_TEST = "Aziz Bela Cora Dmitri Elsa Farid Greta Hugo Ilse Jonas Kira Leif Mina Nils Olga Pavel Rosa Stefan Talia Vera".split()
LAST_TRAIN = (
    "Adams Brown Clark Diaz Evans Frost Garcia Hill Ito Jones Khan Lopez Meyer Nguyen Okoye".split()
)
LAST_TEST = "Aalto Berg Costa Dvorak Eriksen Fischer Gruber Horvat Ivanov Jovanovic Kovacs Larsen Moreau Novak Olsen".split()
STREETS = ["Baker Street", "Maple Avenue", "Harbor Road", "Station Lane", "Oak Drive", "Mill Court"]
# capitalised non-PII words that a naive 'capitalised = name' rule would redact
CONFUSERS = [
    "Monday",
    "Friday",
    "Berlin",
    "Paris",
    "Acme Cloud",
    "Quarterly Report",
    "Python",
    "Slack",
    "Tuesday",
    "Project Falcon",
    "Finance Team",
    "Kubernetes",
]

TEMPLATES = [
    "{NAME} called on {CONF} about the invoice.",
    "Please send the contract to {NAME} at {EMAIL} before {CONF}.",
    "Customer {NAME} lives at {ADDR} and can be reached on {PHONE}.",
    "{CONF} meeting notes: {NAME} will follow up with {NAME}.",
    "Refund to card {CARD} for {NAME}, IBAN {IBAN}.",
    "SSN {SSN} belongs to {NAME}; verify with {CONF}.",
    "The {CONF} release was approved. No personal data here.",
    "Ship to {NAME}, {ADDR}. Contact {PHONE} or {EMAIL}.",
    "{CONF} is down again, ask the {CONF} owners.",
    "Dear {NAME}, your order from {CONF} has shipped.",
]


HELD_OUT_TEMPLATES = [
    "Spoke with {NAME} yesterday regarding {CONF}.",
    "{NAME} (customer since 2019) wrote from {EMAIL}.",
    "Delivery address: {ADDR}. Recipient: {NAME}. Phone {PHONE}.",
    "Escalated by {NAME} to the {CONF} group.",
    "Payment of invoice 4411 by {NAME} using {CARD}.",
    "After the {CONF} review, {NAME} asked for a copy.",
    "Passport check for {NAME}: SSN {SSN}, IBAN {IBAN}.",
    "{CONF} and {CONF} were mentioned, nothing sensitive.",
]  # sentence structures never seen in training


def _name(rng, first, last):
    return f"{rng.choice(first)} {rng.choice(last)}"


def make_text(
    rng: random.Random, test: bool, held_out: bool = False
) -> tuple[str, list[tuple[int, int, str]]]:
    first, last = (FIRST_TEST, LAST_TEST) if test else (FIRST_TRAIN, LAST_TRAIN)
    tpl = rng.choice(HELD_OUT_TEMPLATES if held_out else TEMPLATES)
    vals = {
        "NAME": lambda: ("PERSON", _name(rng, first, last)),
        "ADDR": lambda: ("ADDRESS", f"{rng.randint(1, 999)} {rng.choice(STREETS)}"),
        "EMAIL": lambda: (
            "EMAIL",
            f"{rng.choice(first).lower()}.{rng.choice(last).lower()}@example.com",
        ),
        "PHONE": lambda: ("PHONE", f"555-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}"),
        "CARD": lambda: ("CREDIT_CARD", luhn_number(rng)),
        "IBAN": lambda: ("IBAN", iban_de(rng)),
        "SSN": lambda: (
            "US_SSN",
            f"{rng.randint(100, 665)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}",
        ),
        "CONF": lambda: (None, rng.choice(CONFUSERS)),
    }
    out, spans, i = "", [], 0
    while i < len(tpl):
        if tpl[i] == "{":
            j = tpl.index("}", i)
            label, text = vals[tpl[i + 1 : j]]()
            if label:
                spans.append((len(out), len(out) + len(text), label))
            out += text
            i = j + 1
        else:
            out += tpl[i]
            i += 1
    return out, spans


def make_doc(
    rng: random.Random, test: bool, held_out: bool = False
) -> tuple[str, list[tuple[int, int, str]]]:
    """One to three sentences joined, so names also occur after a full stop and mid-document."""
    text, spans = "", []
    for k in range(rng.choice([1, 2, 3])):
        t, sp = make_text(rng, test, held_out)
        off = len(text) + (1 if k else 0)
        text += (" " if k else "") + t
        spans += [(a + off, b + off, label) for a, b, label in sp]
    return text, spans


def dataset(n: int, seed: int, test: bool, held_out: bool = False):
    rng = random.Random(seed)
    return [make_doc(rng, test, held_out) for _ in range(n)]
