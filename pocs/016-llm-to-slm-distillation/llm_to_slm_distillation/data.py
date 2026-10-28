"""Synthetic support tickets: category-specific phrases mixed with confusable phrases from other categories."""

from __future__ import annotations

import random

CATEGORIES = ("billing", "login", "shipping", "bug", "feature_request", "cancellation")
PHRASES = {
    "billing": [
        "I was charged twice",
        "the invoice amount is wrong",
        "refund has not arrived",
        "my card was declined",
        "VAT is missing on the receipt",
        "update my payment method",
    ],
    "login": [
        "I cannot sign in",
        "password reset email never arrives",
        "two factor code is rejected",
        "my account is locked",
        "the login page keeps looping",
        "single sign on fails",
    ],
    "shipping": [
        "my order has not arrived",
        "the tracking number shows no updates",
        "the parcel was delivered to the wrong address",
        "package arrived damaged",
        "I need to change the delivery date",
        "customs fees are unexpected",
    ],
    "bug": [
        "the app crashes on startup",
        "the export button does nothing",
        "error 500 when saving",
        "the page freezes after uploading",
        "numbers on the dashboard look wrong",
        "notifications are duplicated",
    ],
    "feature_request": [
        "it would be great to have dark mode",
        "please add CSV import",
        "can you support bulk editing",
        "we need an API for reports",
        "add a calendar view please",
        "support for more languages would help",
    ],
    "cancellation": [
        "please cancel my subscription",
        "I want to close my account",
        "stop the renewal",
        "how do I downgrade to the free plan",
        "delete my data and cancel",
        "end my contract at the next term",
    ],
}
FILLER = [
    "thanks",
    "this is urgent",
    "please help",
    "it started yesterday",
    "I already tried restarting",
    "our team is blocked",
    "any update",
    "using the mobile app",
    "on the web version",
    "happened again today",
]


def generate(n: int, seed: int) -> tuple[list[str], list[str]]:
    rng = random.Random(seed)
    texts, labels = [], []
    for _ in range(n):
        cat = rng.choice(CATEGORIES)
        parts = [rng.choice(PHRASES[cat])]
        for _ in range(rng.randint(1, 3)):
            if rng.random() < 0.35:  # a confusable phrase from another category
                parts.append(rng.choice(PHRASES[rng.choice([c for c in CATEGORIES if c != cat])]))
            else:
                parts.append(rng.choice(FILLER) if rng.random() < 0.7 else rng.choice(PHRASES[cat]))
        rng.shuffle(parts)
        texts.append(". ".join(parts))
        labels.append(cat)
    return texts, labels
