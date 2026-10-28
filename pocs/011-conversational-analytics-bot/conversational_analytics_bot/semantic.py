"""Governed semantic layer: the ONLY place SQL fragments live. User text never becomes SQL."""

from __future__ import annotations

COMPLETED = "o.status IN ('completed','shipped')"
FROM = (
    "FROM orders o JOIN customers c ON c.customer_id = o.customer_id "
    "JOIN order_items oi ON oi.order_id = o.order_id JOIN products p ON p.product_id = oi.product_id"
)
METRICS = {
    "revenue": {"sql": "ROUND(SUM(oi.quantity * oi.unit_price), 2)", "desc": "Sum of quantity x unit price on completed or shipped orders"},
    "orders": {"sql": "COUNT(DISTINCT o.order_id)", "desc": "Distinct completed or shipped orders"},
    "units": {"sql": "SUM(oi.quantity)", "desc": "Units sold on completed or shipped orders"},
    "customers": {"sql": "COUNT(DISTINCT c.customer_id)", "desc": "Distinct buying customers"},
}  # fmt: skip
DIMENSIONS = {
    "country": "c.country",
    "segment": "c.segment",
    "category": "p.category",
    "month": "substr(o.order_date, 1, 7)",
    "year": "substr(o.order_date, 1, 4)",
}
# columns that must never be reachable through the bot
FORBIDDEN = {"email", "name", "customer_id", "signup_date"}
OPS = {"eq": "=", "in": "IN"}
MIN_GROUP = 5  # groups with fewer distinct customers are suppressed
MAX_ROWS = 50
ROLES = {
    "analyst": {},
    "manager": {"country": True},
}  # manager rows are restricted to their country
