"""Secret and PII redaction plus a heuristic detector for instruction-like text in untrusted data."""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"
SECRET_PATTERNS = [
    re.compile(r"(?<=://)[^:/\s@]+:[^@\s]+(?=@)"),  # user:password in a connection URL
    re.compile(r"\bCANARY-[0-9a-f]{8,}\b"),  # synthetic planted secrets used by the evaluation
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[A-Za-z]{2,}\b"),  # email addresses
]
# Phrases that address the model rather than describe an incident. A heuristic, not a guarantee:
# the evaluation in POC 079 measures what it misses.
INJECTION_PATTERNS = [
    re.compile(r"(?i)\b(ignore|disregard|forget)\b[^.\n]{0,40}\b(previous|prior|above|earlier)\b"),
    re.compile(r"(?i)\b(you (must|should|are now)|new (instructions?|task)|system prompt)\b"),
    re.compile(
        r"(?i)\b(call|invoke|run|execute|use)\s+(the\s+)?(tool\s+)?`?[a-z_]{4,}`?\s*(tool|with|\()"
    ),
    re.compile(r"(?i)\b(assistant|system|developer)\s*:"),
    re.compile(r"(?i)</?(system|instructions?|tool_call|function_call)>"),
]
FILTERED = "[FILTERED: instruction-like text removed from untrusted data]"


def redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def contains_secret(text: str) -> bool:
    return any(p.search(text) for p in SECRET_PATTERNS)


def looks_like_injection(text: str) -> bool:
    return any(p.search(text) for p in INJECTION_PATTERNS)


def filter_untrusted(text: str) -> tuple[str, bool]:
    """Drop lines/sentences that look like instructions. Returns (clean_text, was_filtered)."""
    if not looks_like_injection(text):
        return text, False
    parts = re.split(r"(?<=[.!?\n])\s+", text)
    kept = [p if not looks_like_injection(p) else FILTERED for p in parts]
    return " ".join(kept), True
