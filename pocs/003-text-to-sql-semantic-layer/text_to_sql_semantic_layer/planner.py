"""Question -> plan/SQL. Three interchangeable front ends, all feeding the same guard + executor:

- RulePlanner: deterministic keyword grammar over the semantic layer (default; no model needed).
- LLMPlanner: asks a local model for a JSON plan, then validates it against the layer.
- LLMSQLWriter: asks a local model for raw SQL (schema-linked prompt). Untrusted by design.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from llm_client import LLMClient
from retrieval_kit import BM25Index

from .schema import SchemaInfo
from .semantic import (
    DIMENSIONS,
    MAX_LIMIT,
    METRICS,
    Filter,
    Plan,
    PlanError,
    describe_layer,
    validate_plan,
)


class Unplannable(ValueError):
    """The question cannot be answered safely from the semantic layer."""


_DIM_WORDS = {
    "country": ("country", "countries"),
    "segment": ("segment", "segments"),
    "category": ("category", "categories"),
    "product": ("product", "products"),
    "month": ("month", "months"),
    "year": ("year", "years"),
    "status": ("status", "statuses"),
}
_ADJECTIVE_DIMS = {"monthly": "month", "yearly": "year", "annual": "year", "annually": "year"}
_METRIC_PATTERNS = [
    (r"average order value|avg order value|aov", "avg_order_value"),
    (r"gross margin|margin|profit", "gross_margin"),
    (r"units sold|units|items sold|number of items|items|quantity", "units_sold"),
    (
        r"unique customers|number of customers|customer count|how many customers|customers",
        "customer_count",
    ),
    (r"number of orders|order count|how many orders|orders", "order_count"),
    (r"revenue|sales|turnover|income", "revenue"),
]
_NUMBER_WORDS = dict(
    zip("one two three four five six seven eight nine ten".split(), range(1, 11), strict=True)
)
_FORBIDDEN = re.compile(
    r"\b(delete|drop|update|insert|remove|alter|truncate|emails?|passwords?|phones?)\b"
)
_STOP = set(
    "a an the of for in on by per each every and or with to is are was were what s which who how "
    "show me give list tell total number during across at as from all my our it its there be has "
    "have did do does broken down generated made placed place most least than".split()
)
_GROUP_MARKER = re.compile(r"\b(by|per|each|every|across)\b")


def _norm(question: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", question.lower())
    for old, new in (
        ("product categories", "categories"),
        ("product category", "category"),
        ("customer segment", "segment"),
        ("customer country", "country"),
    ):
        text = text.replace(old, new)
    return f" {text.strip()} "


class RulePlanner:
    def __init__(self, known_values: Mapping[str, set[str]]):
        self.values = {
            dim: {v.lower(): v for v in vals} for dim, vals in known_values.items()
        }  # dim -> lowercase -> canonical

    def plan(self, question: str) -> Plan:
        text = _norm(question)
        if _FORBIDDEN.search(text):
            raise Unplannable("this assistant only answers read-only aggregate questions")
        matches = [
            (m.start(), -len(m.group()), metric)
            for pattern, metric in _METRIC_PATTERNS
            for m in re.finditer(rf"\b(?:{pattern})\b", text)
        ]
        if not matches:
            raise Unplannable("no known metric (revenue, orders, customers, units, margin, AOV)")
        metric = min(matches)[2]

        consumed = set()  # words explained by something in the plan
        for pattern, _ in _METRIC_PATTERNS:
            for m in re.finditer(rf"\b(?:{pattern})\b", text):
                consumed.update(m.group().split())

        dims: list[str] = []
        marker = _GROUP_MARKER.search(text)
        group_clause = text[marker.end() :] if marker else ""
        for word in group_clause.split():
            for dim, forms in _DIM_WORDS.items():
                if word in forms and dim not in dims:
                    dims.append(dim)
        for word, dim in _ADJECTIVE_DIMS.items():
            if f" {word} " in text and dim not in dims:
                dims.append(dim)
        ranked = re.search(
            r"\b(top|best|highest|largest|biggest|bottom|lowest|worst|smallest|fewest)\s+"
            r"(\d+|" + "|".join(_NUMBER_WORDS) + r")?\s*(\w+)",
            text,
        )
        which = re.search(r"\bwhich (\w+)", text)
        order, limit = None, None
        if ranked:
            word, count, noun = ranked.groups()
            order = "asc" if word in ("bottom", "lowest", "worst", "smallest", "fewest") else "desc"
            if count:
                limit = int(count) if count.isdigit() else _NUMBER_WORDS[count]
                for dim, forms in _DIM_WORDS.items():
                    if noun in forms and dim not in dims:
                        dims.append(dim)
        if which:
            for dim, forms in _DIM_WORDS.items():
                if which.group(1) in forms and dim not in dims:
                    dims.append(dim)
            if order is None and re.search(r"\b(most|highest|largest|biggest|best)\b", text):
                order, limit = "desc", 1
            elif order is None and re.search(r"\b(least|lowest|fewest|worst|smallest)\b", text):
                order, limit = "asc", 1
        if limit is None and ranked and ranked.group(2) is None:
            limit = 1 if which else None
        if not dims:
            order = limit = None  # ranking needs something to rank

        filters = []
        for dim, vals in self.values.items():
            found = tuple(
                canon for low, canon in vals.items() if re.search(rf"\b{re.escape(low)}\b", text)
            )
            if found:
                filters.append(Filter(dim, found))
                for low in vals:
                    consumed.update(low.split())
        years = tuple(re.findall(r"\b(20\d\d)\b", text))
        if years:
            filters.append(Filter("year", years))
            consumed.update(years)

        for forms in _DIM_WORDS.values():
            consumed.update(forms)
        consumed.update(_ADJECTIVE_DIMS)
        consumed.update(_NUMBER_WORDS)
        consumed.update(
            (
                "top",
                "best",
                "highest",
                "largest",
                "biggest",
                "bottom",
                "lowest",
                "worst",
                "smallest",
                "fewest",
                "which",
            )
        )
        leftover = [
            w
            for w in group_clause.split()
            if w not in consumed and w not in _STOP and not w.isdigit()
        ]
        if leftover:
            raise Unplannable(f"unsupported grouping or qualifier: {' '.join(leftover)!r}")

        plan = Plan(metric, tuple(dims), tuple(filters), order, limit)
        try:
            validate_plan(plan)
        except PlanError as exc:
            raise Unplannable(str(exc)) from exc
        return plan


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise Unplannable("model reply contained no JSON object")
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError as exc:
        raise Unplannable(f"model reply was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise Unplannable("model reply JSON was not an object")
    return data


class LLMPlanner:
    """Local model proposes a plan; nothing it says is trusted beyond `validate_plan`."""

    def __init__(self, client: LLMClient, model: str):
        self.client, self.model = client, model

    def plan(self, question: str) -> Plan:
        prompt = (
            "You translate analytics questions into a JSON query plan. Reply with JSON only.\n"
            f"{describe_layer()}\n"
            'Schema: {"metric": str, "dimensions": [str], "filters": [{"dimension": str, '
            '"values": [str]}], "order": "asc"|"desc"|null, "limit": int|null}\n'
            f'Limit is at most {MAX_LIMIT}. Years are strings like "2024". If the question cannot '
            'be answered with these metrics and dimensions reply {"error": "unsupported"}.\n'
            f"Question: {question}"
        )
        data = _extract_json(self.client.generate(prompt, model=self.model, max_tokens=200).text)
        if "error" in data:
            raise Unplannable(f"model declined: {data['error']}")
        try:
            plan = Plan(
                metric=data["metric"],
                dimensions=tuple(data.get("dimensions") or ()),
                filters=tuple(
                    Filter(f["dimension"], tuple(str(v) for v in f["values"]))
                    for f in data.get("filters") or ()
                ),
                order=data.get("order"),
                limit=data.get("limit"),
            )
            validate_plan(plan)
        except (KeyError, TypeError, PlanError) as exc:
            raise Unplannable(f"model produced an invalid plan: {exc}") from exc
        return plan


def link_tables(question: str, schema: SchemaInfo, k: int = 3) -> list[str]:
    """Schema linking: BM25 over table/column names, then pull in one-hop foreign-key neighbours."""
    names = list(schema.tables)
    docs = [f"{n} " + " ".join(c.name for c in schema.tables[n]) for n in names]
    top = [t for t, _ in BM25Index().fit(names, docs).search(question, k)] or names[:k]
    for table, _, ref_table, _ in schema.foreign_keys:
        if table in top and ref_table not in top and len(top) < k + 2:
            top.append(ref_table)
    return top


class LLMSQLWriter:
    """Local model writes raw SQL for the linked schema subset. Output is untrusted input."""

    def __init__(self, client: LLMClient, model: str):
        self.client, self.model = client, model

    def write(self, question: str, schema: SchemaInfo) -> str:
        tables = link_tables(question, schema)
        prompt = (
            "Write one read-only SQLite SELECT query answering the question. Reply with SQL only.\n"
            f"{schema.describe(tables)}\nQuestion: {question}"
        )
        text = self.client.generate(prompt, model=self.model, max_tokens=300).text
        fenced = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        return (fenced.group(1) if fenced else text).strip()


__all__ = [
    "DIMENSIONS",
    "METRICS",
    "LLMPlanner",
    "LLMSQLWriter",
    "RulePlanner",
    "Unplannable",
    "link_tables",
]
