"""Semantic layer: governed metrics and dimensions compiled to parameterised SQL.

Compiling from a small, validated plan (instead of letting a model write SQL) means joins and
metric definitions are correct by construction and user-supplied values are bound parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

METRICS: dict[str, tuple[str, str]] = {
    "revenue": ("ROUND(SUM(oi.quantity * oi.unit_price), 2)", "sum of quantity x unit price"),
    "units_sold": ("SUM(oi.quantity)", "total units"),
    "order_count": ("COUNT(DISTINCT o.order_id)", "distinct orders"),
    "customer_count": ("COUNT(DISTINCT o.customer_id)", "distinct customers with a matching order"),
    "avg_order_value": (
        "ROUND(SUM(oi.quantity * oi.unit_price) * 1.0 / COUNT(DISTINCT o.order_id), 2)",
        "revenue divided by distinct orders",
    ),
    "gross_margin": (
        "ROUND(SUM(oi.quantity * (oi.unit_price - p.unit_cost)), 2)",
        "revenue minus product cost",
    ),
}
DIMENSIONS: dict[str, str] = {
    "country": "c.country",
    "segment": "c.segment",
    "category": "p.category",
    "product": "p.name",
    "month": "SUBSTR(o.order_date, 1, 7)",
    "year": "SUBSTR(o.order_date, 1, 4)",
    "status": "o.status",
}
_DIM_ALIAS = {"country": "c", "segment": "c", "category": "p", "product": "p"}
_JOINS = {
    "c": "JOIN customers c ON c.customer_id = o.customer_id",
    "p": "JOIN products p ON p.product_id = oi.product_id",
}
DEFAULT_STATUS = "completed"  # metrics count completed orders unless status is dimension/filter
MAX_LIMIT = 100


class PlanError(ValueError):
    """The plan names something the semantic layer does not define."""


@dataclass(frozen=True)
class Filter:
    dimension: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    metric: str
    dimensions: tuple[str, ...] = ()
    filters: tuple[Filter, ...] = ()
    order: str | None = None  # "desc" | "asc" | None (None = dimensions ascending)
    limit: int | None = None


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    params: tuple[str, ...] = field(default_factory=tuple)


def validate_plan(plan: Plan) -> None:
    if plan.metric not in METRICS:
        raise PlanError(f"unknown metric {plan.metric!r}; choose from {sorted(METRICS)}")
    for d in (*plan.dimensions, *(f.dimension for f in plan.filters)):
        if d not in DIMENSIONS:
            raise PlanError(f"unknown dimension {d!r}; choose from {sorted(DIMENSIONS)}")
    if len(set(plan.dimensions)) != len(plan.dimensions):
        raise PlanError("duplicate dimension")
    if plan.order not in (None, "asc", "desc"):
        raise PlanError("order must be asc, desc or null")
    if plan.limit is not None and not 1 <= plan.limit <= MAX_LIMIT:
        raise PlanError(f"limit must be between 1 and {MAX_LIMIT}")
    for f in plan.filters:
        if not f.values or not all(isinstance(v, str) and len(v) <= 64 for v in f.values):
            raise PlanError(f"filter on {f.dimension!r} needs 1+ short string values")


def compile_plan(plan: Plan) -> CompiledQuery:
    validate_plan(plan)
    filtered_dims = {f.dimension for f in plan.filters}
    uses_status = "status" in plan.dimensions or "status" in filtered_dims
    aliases = {_DIM_ALIAS[d] for d in (*plan.dimensions, *filtered_dims) if d in _DIM_ALIAS}
    if plan.metric == "gross_margin":
        aliases.add("p")

    select = [f"{DIMENSIONS[d]} AS {d}" for d in plan.dimensions]
    select.append(f"{METRICS[plan.metric][0]} AS {plan.metric}")
    sql = [
        f"SELECT {', '.join(select)}",
        "FROM order_items oi JOIN orders o ON o.order_id = oi.order_id",
        *(_JOINS[a] for a in sorted(aliases)),
    ]
    params: list[str] = []
    where = []
    if not uses_status:
        where.append("o.status = ?")
        params.append(DEFAULT_STATUS)
    for f in plan.filters:
        marks = ", ".join("?" for _ in f.values)
        where.append(f"{DIMENSIONS[f.dimension]} IN ({marks})")
        params.extend(f.values)
    if where:
        sql.append("WHERE " + " AND ".join(where))
    if plan.dimensions:
        sql.append("GROUP BY " + ", ".join(DIMENSIONS[d] for d in plan.dimensions))
    order = [f"{plan.metric} {plan.order.upper()}"] if plan.order else []
    order += [f"{d} ASC" for d in plan.dimensions]
    if order and plan.dimensions:
        sql.append("ORDER BY " + ", ".join(order))
    if plan.limit:
        sql.append(f"LIMIT {int(plan.limit)}")
    return CompiledQuery(" ".join(sql), tuple(params))


def describe_layer() -> str:
    metrics = "\n".join(f"- {m}: {d}" for m, (_, d) in METRICS.items())
    return f"Metrics:\n{metrics}\nDimensions: {', '.join(DIMENSIONS)}"
