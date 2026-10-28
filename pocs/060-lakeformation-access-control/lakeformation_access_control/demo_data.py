"""Synthetic lake: customers (with PII) and orders, a tag taxonomy, roles and grants."""

from __future__ import annotations

import duckdb

from .model import Catalog, Column, DataFilter, Grant, Table

REGIONS = ["EU", "US", "APAC"]


def build() -> tuple[Catalog, duckdb.DuckDBPyConnection]:
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE customers(customer_id INTEGER, name VARCHAR, email VARCHAR, region VARCHAR, segment VARCHAR)"
    )
    con.execute(
        "CREATE TABLE orders(order_id INTEGER, customer_id INTEGER, region VARCHAR, amount DOUBLE, card_last4 VARCHAR)"
    )
    con.execute(
        "INSERT INTO customers SELECT i, 'Customer ' || i, 'user' || i || '@example.com', ['EU','US','APAC'][1 + i % 3], ['smb','enterprise'][1 + i % 2] FROM range(1, 91) t(i)"
    )
    con.execute(
        "INSERT INTO orders SELECT i, 1 + i % 90, ['EU','US','APAC'][1 + i % 3], round(10 + (i % 17) * 7.5, 2), lpad(CAST(i % 10000 AS VARCHAR), 4, '0') FROM range(1, 301) t(i)"
    )
    cat = Catalog()
    cat.add_table(
        Table(
            "sales",
            "customers",
            [
                Column("customer_id", "int"),
                Column("name", "string"),
                Column("email", "string"),
                Column("region", "string"),
                Column("segment", "string"),
            ],
        )
    )
    cat.add_table(
        Table(
            "sales",
            "orders",
            [
                Column("order_id", "int"),
                Column("customer_id", "int"),
                Column("region", "string"),
                Column("amount", "double"),
                Column("card_last4", "string"),
            ],
        )
    )
    cat.define_tag("sensitivity", {"public", "internal", "pii"})
    cat.define_tag("domain", {"sales", "hr"})
    cat.tag_database("sales", sensitivity="internal", domain="sales")
    cat.tag_column("sales.customers", "name", sensitivity="pii")
    cat.tag_column("sales.customers", "email", sensitivity="pii")
    cat.tag_column("sales.orders", "card_last4", sensitivity="pii")
    cat.tag_column("sales.customers", "region", sensitivity="public")
    cat.add_filter(
        DataFilter(
            "eu_only_customers",
            "sales",
            "customers",
            row_filter="region = 'EU'",
            excluded_columns=("email",),
        )
    )
    cat.add_filter(
        DataFilter(
            "eu_orders",
            "sales",
            "orders",
            row_filter="region = 'EU'",
            excluded_columns=("card_last4",),
        )
    )
    # analyst_eu: EU rows only, no email; data_scientist: every non-PII column via tags; compliance: everything; support: customers only
    cat.grant(
        Grant("analyst_eu", frozenset({"SELECT", "DESCRIBE"}), data_filter="eu_only_customers")
    )
    cat.grant(Grant("analyst_eu", frozenset({"SELECT", "DESCRIBE"}), data_filter="eu_orders"))
    cat.grant(
        Grant(
            "data_scientist",
            frozenset({"SELECT", "DESCRIBE"}),
            tag_expression=(("sensitivity", ("public", "internal")), ("domain", ("sales",))),
        )
    )
    cat.grant(Grant("compliance", frozenset({"SELECT", "DESCRIBE"}), database="sales"))
    cat.grant(
        Grant(
            "support",
            frozenset({"SELECT", "DESCRIBE"}),
            table="sales.customers",
            columns=("customer_id", "name", "region"),
        )
    )
    return cat, con
