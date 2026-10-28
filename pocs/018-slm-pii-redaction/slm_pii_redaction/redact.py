"""Hybrid redactor: validated pattern detectors for structured PII + the trained tagger for names/addresses."""

from __future__ import annotations

import hashlib
import hmac

from agent_policy_guardrails.detectors import DETECTORS

from .tagger import Tagger

STRUCTURED = {
    "credit_card": "CREDIT_CARD",
    "iban": "IBAN",
    "us_ssn": "US_SSN",
    "email": "EMAIL",
    "phone": "PHONE",
}
PRIORITY = {
    "CREDIT_CARD": 0,
    "IBAN": 0,
    "US_SSN": 0,
    "EMAIL": 0,
    "PHONE": 1,
    "ADDRESS": 2,
    "PERSON": 3,
}
MAX_CHARS = 100_000


def detect(text: str, tagger: Tagger | None) -> list[tuple[int, int, str]]:
    if len(text) > MAX_CHARS:
        raise ValueError("text too large")
    spans = []
    for name, label in STRUCTURED.items():
        spans += [(s, e, label) for s, e, _ in DETECTORS[name](text)]
    if tagger is not None:
        spans += tagger.predict_spans(text)
    spans.sort(key=lambda s: (PRIORITY[s[2]], -(s[1] - s[0]), s[0]))
    kept: list[tuple[int, int, str]] = []
    for s in spans:  # resolve overlaps: higher priority (structured, validated) wins
        if not any(s[0] < k[1] and k[0] < s[1] for k in kept):
            kept.append(s)
    return sorted(kept)


def redact(
    text: str, tagger: Tagger | None, mode: str = "typed", salt: bytes = b""
) -> tuple[str, list[tuple[int, int, str]]]:
    """mode 'typed' -> [EMAIL]; 'pseudonym' -> [PERSON_3fa2], stable per value and salt, not reversible without the salt."""
    if mode not in {"typed", "pseudonym"}:
        raise ValueError("mode must be typed or pseudonym")
    if mode == "pseudonym" and len(salt) < 8:
        raise ValueError("pseudonym mode needs a salt of at least 8 bytes")
    spans = detect(text, tagger)
    out, last = [], 0
    for s, e, label in spans:
        out.append(text[last:s])
        if mode == "typed":
            out.append(f"[{label}]")
        else:
            tag = hmac.new(salt, text[s:e].strip().lower().encode(), hashlib.sha256).hexdigest()[:6]
            out.append(f"[{label}_{tag}]")
        last = e
    out.append(text[last:])
    return "".join(out), spans
