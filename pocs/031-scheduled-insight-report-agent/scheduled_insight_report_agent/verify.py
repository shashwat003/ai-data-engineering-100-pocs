"""Number-grounding and safety checks for generated narratives."""

from __future__ import annotations

import re

NUM = re.compile(r"(?<![\w.])\$?-?\d[\d,]*(?:\.\d+)?(?:%|[kKmM]\b)?")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
URL = re.compile(r"(?i)https?://|www\.|!\[[^\]]*\]\(|\]\(")


def _flatten(v):
    if isinstance(v, bool):
        return
    if isinstance(v, (int, float)):
        yield float(v)
    elif isinstance(v, dict):
        for x in v.values():
            yield from _flatten(x)
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _flatten(x)


def allowed_numbers(facts: dict) -> list[float]:
    base = [abs(x) for x in _flatten({k: v for k, v in facts.items() if k not in {"period"}})]
    derived = [
        abs(facts["revenue"] - facts["revenue_prev"]),
        abs(facts["orders"] - facts["orders_prev"]),
    ]
    return base + derived + [float(facts["days"]), 4.0]  # "four-week"


def parse_number(tok: str) -> float:
    t = tok.replace("$", "").replace(",", "").rstrip("%")
    mult = 1.0
    if t and t[-1] in "kKmM":
        mult = 1e3 if t[-1] in "kK" else 1e6
        t = t[:-1]
    return abs(float(t)) * mult


def check(text: str, facts: dict) -> dict:
    """Return {ok, ungrounded numbers, links}. Dates are removed first; every other number must match a fact."""
    scrubbed = DATE.sub(" ", text)
    allowed = allowed_numbers(facts)
    bad = []
    for m in NUM.finditer(scrubbed):
        try:
            x = parse_number(m.group())
        except ValueError:
            continue
        pct = m.group().endswith("%")
        tol = 0.051 if (pct or x < 1000) else 0.005 * x
        if not any(abs(x - a) <= tol for a in allowed):
            bad.append(m.group())
    links = URL.findall(text)
    return {"ok": not bad and not links, "ungrounded_numbers": bad, "links": links}
