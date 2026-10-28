"""Scan database columns for PII: name hints + value detectors (with checksums) + sampling, in DuckDB.

Decision per column: combine (a) the column name, (b) the share of sampled values matching a detector,
and (c) the class's precision (checksummed detectors need fewer matches). Output is a classification
with a confidence, never the values themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import duckdb
from agent_policy_guardrails.detectors import DETECTORS

NAME_HINTS = {
    "email": re.compile(r"e[-_]?mail", re.I),
    "phone": re.compile(r"phone|mobile|tel(ephone)?\b|fax", re.I),
    "us_ssn": re.compile(r"ssn|social[-_]?sec", re.I),
    "credit_card": re.compile(r"card[-_]?(no|num)|pan\b|cc[-_]?num", re.I),
    "iban": re.compile(r"iban|bank[-_]?acc", re.I),
    "person_name": re.compile(
        r"(first|last|full|sur|given)[-_]?name|^name$|customer[-_]?name", re.I
    ),
    "address": re.compile(r"address|street|postcode|zip", re.I),
    "dob": re.compile(r"birth|dob", re.I),
}
VALUE_CLASSES = {
    "email": "email",
    "phone": "phone",
    "us_ssn": "us_ssn",
    "credit_card": "credit_card",
    "iban": "iban",
}
# minimum share of sampled values that must match before a column is classified from values alone
MIN_SHARE = {
    "credit_card": 0.5,
    "iban": 0.2,
    "us_ssn": 0.3,
    "email": 0.2,
    "phone": 0.5,
}  # random 16-digit strings pass Luhn ~10% of the time, so cards need a majority
DOB = re.compile(r"^(19|20)\d\d-\d\d-\d\d$")


@dataclass(frozen=True)
class Finding:
    table: str
    column: str
    pii_class: str | None
    confidence: float
    evidence: str  # which signals fired; never contains values


def q(identifier: str) -> str:
    """Quote an identifier for DuckDB (double any embedded quote)."""
    return '"' + identifier.replace('"', '""') + '"'


def classify_column(name: str, values: list[str]) -> tuple[str | None, float, str]:
    values = [v for v in values if v is not None and str(v).strip() != ""]
    hint = next((c for c, rx in NAME_HINTS.items() if rx.search(name)), None)
    shares = {}
    for cls, det in VALUE_CLASSES.items():
        hits = sum(bool(DETECTORS[det](str(v))) for v in values)
        shares[cls] = hits / len(values) if values else 0.0
    if (
        values and hint == "dob"
    ):  # a date alone is ambiguous (signup_date looks the same), so DOB needs the name too
        shares["dob"] = sum(bool(DOB.match(str(v))) for v in values) / len(values)
    best = max(shares, key=shares.get) if shares else None
    # a pure-digit column must not be called 'phone' on the pattern alone (order numbers look like phones)
    if best == "phone" and not (hint == "phone") and shares["phone"] < 0.9:
        best = None
    if best and shares[best] >= MIN_SHARE.get(best, 0.5):
        conf = min(0.99, 0.55 + 0.4 * shares[best] + (0.15 if hint == best else 0.0))
        return (
            best,
            round(conf, 2),
            f"values:{best}={shares[best]:.0%}" + (" + name hint" if hint == best else ""),
        )
    if hint:
        # name suggests PII but values do not confirm it (e.g. free-text names/addresses have no checksum)
        conf = 0.5 if hint in ("person_name", "address", "dob") else 0.3
        return hint, conf, "name hint only"
    return None, 0.0, "no signal"


def scan(con: duckdb.DuckDBPyConnection, sample: int = 500, seed: int = 0) -> list[Finding]:
    out = []
    for (table,) in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall():
        cols = [
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
                [table],
            ).fetchall()
        ]
        for col in cols:
            rows = con.execute(
                f"SELECT CAST({q(col)} AS VARCHAR) FROM {q(table)} USING SAMPLE reservoir({int(sample)} ROWS) REPEATABLE ({int(seed)})"
            ).fetchall()
            cls, conf, ev = classify_column(col, [r[0] for r in rows])
            out.append(Finding(table, col, cls, conf, ev))
    return out


def free_text_pii(
    con: duckdb.DuckDBPyConnection, table: str, column: str, limit: int = 2000
) -> dict[str, int]:
    """Count embedded PII spans inside a free-text column (counts only, never the content)."""
    counts: dict[str, int] = {}
    for (v,) in con.execute(
        f"SELECT CAST({q(column)} AS VARCHAR) FROM {q(table)} LIMIT {int(limit)}"
    ).fetchall():
        for cls, det in VALUE_CLASSES.items():
            if DETECTORS[det](v or ""):
                counts[cls] = counts.get(cls, 0) + 1
    return counts


def mask_value(cls: str, v: str) -> str:
    if cls == "email":
        user, _, dom = v.partition("@")
        return user[:1] + "***@" + dom
    if cls in ("credit_card", "iban", "us_ssn", "phone"):
        digits = re.sub(r"\D", "", v)
        return "*" * max(len(digits) - 4, 0) + digits[-4:]
    return "[MASKED]"
