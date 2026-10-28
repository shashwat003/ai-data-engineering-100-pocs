"""SIMULATED natural-language planner (keyword rules emitting the same JSON a tool-calling LLM would).

Real LLM planning is not measured here. What is tested is everything AFTER the planner: validation,
governance and SQL compilation, which must hold for any planner output, including hostile ones.
"""

from __future__ import annotations

import re

from .semantic import DIMENSIONS, METRICS

MONTHS = {
    m: i + 1
    for i, m in enumerate(
        "january february march april may june july august september october november december".split()
    )
}
COUNTRIES = [
    "United States",
    "Germany",
    "India",
    "Brazil",
    "Japan",
    "United Kingdom",
    "Canada",
    "Australia",
]
CATS = ["Electronics", "Home", "Books", "Toys", "Outdoors"]
SEGS = ["consumer", "smb", "enterprise"]


def plan(question: str) -> dict:
    q = question.lower()
    if re.search(r"\b(drop|delete|update|insert|select)\b|;|--", q):
        return {
            "tool": "run_sql",
            "sql": question,
        }  # a planner may emit this; the validator must refuse it
    if "what metrics" in q or "what can you" in q:
        return {"tool": "describe_metrics"}
    metric = next((m for m in METRICS if m in q or (m == "orders" and "order" in q)), None)
    if metric is None and re.search(r"sales|income|turnover", q):
        metric = "revenue"
    if metric is None:
        metric = "revenue" if re.search(r"email|name|phone|address", q) else None
    call: dict = {"tool": "query_metric", "metric": metric, "group_by": [], "filters": []}
    for d in DIMENSIONS:
        if re.search(rf"\b(by|per) {d}\b|\bacross {d}\b", q):
            call["group_by"].append(d)
    for tok in re.findall(r"\b(email|emails|name|names|customer_id|signup_date)\b", q):
        call["group_by"].append(
            tok.rstrip("s") if tok.endswith("s") and tok != "customer_id" else tok
        )
    for c in COUNTRIES:
        if c.lower() in q:
            call["filters"].append({"dim": "country", "op": "eq", "value": c})
    for c in CATS:
        if c.lower() in q:
            call["filters"].append({"dim": "category", "op": "eq", "value": c})
    for s in SEGS:
        if re.search(rf"\b{s}\b", q):
            call["filters"].append({"dim": "segment", "op": "eq", "value": s})
    y = re.search(r"\b(20\d\d)\b", q)
    mo = next((m for m in MONTHS if m in q), None)
    if y and mo:
        call["filters"].append(
            {"dim": "month", "op": "eq", "value": f"{y.group(1)}-{MONTHS[mo]:02d}"}
        )
    elif y:
        call["filters"].append({"dim": "year", "op": "eq", "value": y.group(1)})
    if m := re.search(r"top (\d+)", q):
        call["limit"] = int(m.group(1))
    return call
