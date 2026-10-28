"""Builds the synthetic demo database: the shared retail warehouse plus three derived views."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from data_gen.warehouse import build_warehouse

VIEWS = """
CREATE VIEW daily_orders AS
    SELECT order_date, COUNT(*) AS orders, COUNT(DISTINCT customer_id) AS customers
    FROM orders GROUP BY order_date;
CREATE VIEW order_revenue AS
    SELECT o.order_id, o.customer_id, o.order_date, o.status,
           ROUND(SUM(oi.quantity * oi.unit_price), 2) AS revenue
    FROM orders o JOIN order_items oi ON oi.order_id = o.order_id GROUP BY o.order_id;
CREATE VIEW product_margin AS
    SELECT p.product_id, p.name, p.category,
           ROUND(SUM(oi.quantity * (oi.unit_price - p.unit_cost)), 2) AS margin
    FROM products p JOIN order_items oi ON oi.product_id = p.product_id GROUP BY p.product_id;
"""


def build_demo_database(path: str | Path, seed: int = 3) -> None:
    build_warehouse(path, seed=seed)
    con = sqlite3.connect(path)
    try:
        con.executescript(VIEWS)
        con.commit()
    finally:
        con.close()
