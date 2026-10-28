"""Detectors return spans (start, end, label). Checksums cut false positives on number-like strings."""

from __future__ import annotations

import re

Span = tuple[int, int, str]


def luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def iban_ok(iban: str) -> bool:
    s = iban.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", s):
        return False
    rearranged = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1


CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(
    r"(?<!\d)(?:\+\d{1,3}[ -]?)?(?:\(\d{2,4}\)[ -]?)?\d{3}[ -]\d{3,4}[ -]?\d{0,4}(?!\d)"
)
SECRET = re.compile(r"\b(?:sk|pk|tok)[-_][A-Za-z0-9]{16,}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----")
INJECTION = re.compile(
    r"(?i)\b(ignore|disregard|forget)\b[^.\n]{0,40}\b(previous|prior|above|all)\b[^.\n]{0,30}\b(instructions?|rules?|prompt)\b|\byou are now\b|\bsystem prompt\b|\bjailbreak\b|\bdeveloper mode\b"
)
SELF_HARM = re.compile(r"(?i)\b(kill myself|end my life|suicide|self[- ]harm|hurt myself)\b")
MALWARE = re.compile(
    r"(?i)\b(write|build|create|give me)\b[^.\n]{0,40}\b(ransomware|keylogger|botnet|malware|computer virus)\b"
)


def credit_card(text: str, **_) -> list[Span]:
    out = []
    for m in CARD.finditer(text):
        digits = re.sub(r"[ -]", "", m.group())
        if 13 <= len(digits) <= 19 and luhn_ok(digits):
            out.append((m.start(), m.end(), "credit_card"))
    return out


def iban(text: str, **_) -> list[Span]:
    return [(m.start(), m.end(), "iban") for m in IBAN.finditer(text) if iban_ok(m.group())]


def _re(p, label):
    return lambda text, **_: [(m.start(), m.end(), label) for m in p.finditer(text)]


def too_long(text: str, max_chars: int = 4000, **_) -> list[Span]:
    return [(0, len(text), "too_long")] if len(text) > max_chars else []


DETECTORS = {
    "credit_card": credit_card, "iban": iban, "us_ssn": _re(SSN, "us_ssn"), "email": _re(EMAIL, "email"), "phone": _re(PHONE, "phone"),
    "secret_token": _re(SECRET, "secret"), "injection_phrase": _re(INJECTION, "injection"), "topic_self_harm": _re(SELF_HARM, "self_harm"),
    "topic_malware": _re(MALWARE, "malware"), "too_long": too_long,
}  # fmt: skip
