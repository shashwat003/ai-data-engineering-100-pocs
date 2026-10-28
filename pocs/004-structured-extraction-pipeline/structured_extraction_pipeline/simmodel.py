"""SIMULATED extraction model. Not an LLM: a regex reader that makes seeded, realistic format errors.

It exists so retry behaviour is measurable offline. How a real model responds to validation feedback
is NOT measured here; `repair_prob` is an assumption, and the README says so.
"""

from __future__ import annotations

import hashlib
import json
import re

from llm_client import LLMResponse, estimate_tokens

MONTH = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
FAULTS = [
    "fenced",
    "trailing_comma",
    "bad_date",
    "string_amount",
    "wrong_total",
    "missing_field",
    "bad_currency",
    "prose",
]


def _u(*parts) -> float:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def read_doc(text: str) -> dict:
    """Ground-truth-free reader: parse the document text with regexes (ignores injected instructions)."""
    g = lambda pat: re.search(pat, text, re.M)  # noqa: E731
    money = lambda s: float(s.replace(",", ""))  # noqa: E731
    date_s = g(r"^Date: (.+)$").group(1)
    if re.match(r"\d{4}-\d{2}-\d{2}", date_s):
        iso = date_s
    else:
        m = re.match(r"(?:(\w{3}) (\d{1,2}), (\d{4})|(\d{1,2}) (\w{3}) (\d{4}))", date_s)
        mon, day, yr = (
            (m.group(1), m.group(2), m.group(3))
            if m.group(1)
            else (m.group(5), m.group(4), m.group(6))
        )
        iso = f"{int(yr):04d}-{MONTH[mon]:02d}-{int(day):02d}"
    items = [
        {"description": d, "qty": int(q), "unit_price": money(p)}
        for q, d, p in re.findall(
            r"^\s+(\d+) x (.+?) @ (?:\$|EUR |GBP )([\d,]+\.\d\d)$", text, re.M
        )
    ]
    amt = lambda label: money(g(rf"^{label}: (?:\$|EUR |GBP )([\d,]+\.\d\d)$").group(1))  # noqa: E731
    return {
        "vendor": g(r"^From: (.+)$").group(1),
        "invoice_number": g(r"^INVOICE (\S+)$").group(1),
        "invoice_date": iso,
        "currency": g(r"^Currency: (\w+)$").group(1),
        "line_items": items,
        "subtotal": amt("Subtotal"),
        "tax": amt("Tax"),
        "total": amt("TOTAL DUE"),
    }


def corrupt(d: dict, fault: str) -> str:
    d = json.loads(json.dumps(d))
    if fault == "bad_date":
        y, m, dd = d["invoice_date"].split("-")
        d["invoice_date"] = f"{dd}/{m}/{y}"
    elif fault == "string_amount":
        d["total"] = f"{d['total']:,.2f}"
    elif fault == "wrong_total":
        d["total"] = round(d["total"] + 10, 2)
    elif fault == "missing_field":
        d.pop("tax")
    elif fault == "bad_currency":
        d["currency"] = {"USD": "US$", "EUR": "euro", "GBP": "pounds"}[d["currency"]]
    out = json.dumps(d)
    if fault == "fenced":
        return f"```json\n{out}\n```"
    if fault == "trailing_comma":
        return out[:-1] + ",}"
    if fault == "prose":
        return f"Sure! Here is the extracted data: {out} Let me know if you need anything else."
    return out


class SimExtractor:
    """First attempts are faulty with `fault_rate`; with feedback a fault persists w.p. 1 - repair_prob."""

    def __init__(self, fault_rate: float = 0.55, repair_prob: float = 0.8, seed: int = 0):
        self.fault_rate, self.repair_prob, self.seed = fault_rate, repair_prob, seed
        self.calls: list[str] = []

    def generate(
        self,
        prompt: str,
        *,
        model: str = "sim-extractor",
        temperature: float = 0.0,
        max_tokens: int = 512,
    ):
        self.calls.append(prompt)
        doc = prompt.split("<document>\n", 1)[1].split("\n</document>", 1)[0]
        key = hashlib.sha256(doc.encode()).hexdigest()[:12]
        am = re.search(r"VALIDATION ERRORS after attempt (\d+)", prompt)
        attempt = int(am.group(1)) if am else 0
        data = read_doc(doc)
        fault = FAULTS[int(_u(self.seed, key, "kind") * len(FAULTS))]
        if attempt == 0:
            faulty = _u(self.seed, key, "first") < self.fault_rate
        else:
            faulty = _u(self.seed, key, "retry", attempt) >= self.repair_prob
        text = corrupt(data, fault) if faulty else json.dumps(data)
        return LLMResponse(text, model, estimate_tokens(prompt), estimate_tokens(text), 0.0)
