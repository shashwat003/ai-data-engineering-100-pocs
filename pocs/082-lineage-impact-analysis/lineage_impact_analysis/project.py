"""A small analytics SQL project with hand-written ground-truth column lineage (one hop)."""

from __future__ import annotations

RAW = {
    "raw": {
        "orders": {
            "order_id": "int",
            "customer_id": "int",
            "amount": "double",
            "status": "text",
            "created_at": "timestamp",
        },
        "customers": {
            "customer_id": "int",
            "name": "text",
            "email": "text",
            "segment": "text",
            "country": "text",
        },
        "order_items": {
            "order_id": "int",
            "product_id": "int",
            "quantity": "int",
            "unit_price": "double",
        },
        "products": {"product_id": "int", "category": "text", "unit_cost": "double"},
    }
}

# model -> SQL. Output columns are explicit so lineage can be resolved without running anything.
MODELS = {
    "staging.stg_orders": "SELECT order_id, customer_id, CAST(amount AS DOUBLE) AS amount_usd, LOWER(status) AS status, CAST(created_at AS DATE) AS order_date FROM raw.orders",
    "staging.stg_customers": "SELECT customer_id, UPPER(country) AS country, segment, SPLIT_PART(email, '@', 2) AS email_domain FROM raw.customers",
    "staging.stg_items": "SELECT i.order_id, i.product_id, i.quantity, i.quantity * i.unit_price AS line_revenue, i.quantity * p.unit_cost AS line_cost, p.category FROM raw.order_items AS i JOIN raw.products AS p ON p.product_id = i.product_id",
    "intermediate.int_order_margin": "SELECT order_id, SUM(line_revenue) AS revenue, SUM(line_revenue) - SUM(line_cost) AS margin FROM staging.stg_items GROUP BY order_id",
    "marts.fct_orders": (
        "WITH paid AS (SELECT * FROM staging.stg_orders WHERE status = 'paid') "
        "SELECT p.order_id, p.customer_id, p.order_date, p.amount_usd, m.margin, ROW_NUMBER() OVER (PARTITION BY p.customer_id ORDER BY p.order_date) AS order_seq "
        "FROM paid AS p LEFT JOIN intermediate.int_order_margin AS m ON m.order_id = p.order_id"
    ),
    "marts.dim_customer": (
        "SELECT c.customer_id, c.country, c.segment, COUNT(f.order_id) AS orders, SUM(f.amount_usd) AS lifetime_value, MIN(f.order_date) AS first_order_date "
        "FROM staging.stg_customers AS c LEFT JOIN marts.fct_orders AS f ON f.customer_id = c.customer_id GROUP BY c.customer_id, c.country, c.segment"
    ),
    "marts.revenue_by_segment": (
        "SELECT segment, country, SUM(lifetime_value) AS revenue, SUM(orders) AS orders FROM marts.dim_customer GROUP BY segment, country"
    ),
    "marts.category_margin": (
        "SELECT category, SUM(line_revenue) AS revenue, SUM(line_revenue - line_cost) AS margin FROM staging.stg_items GROUP BY category"
    ),
    "marts.exec_dashboard": (
        "SELECT r.segment, r.revenue, r.orders, c.margin AS best_category_margin FROM marts.revenue_by_segment AS r CROSS JOIN (SELECT margin FROM marts.category_margin ORDER BY margin DESC LIMIT 1) AS c"
    ),
    "marts.email_domains": "SELECT email_domain, COUNT(*) AS customers FROM staging.stg_customers GROUP BY email_domain",
}

# Ground truth (one hop): "model.column" -> set of "upstream_table.column"
TRUTH = {
    "staging.stg_orders.amount_usd": {"raw.orders.amount"},
    "staging.stg_orders.status": {"raw.orders.status"},
    "staging.stg_orders.order_date": {"raw.orders.created_at"},
    "staging.stg_customers.email_domain": {"raw.customers.email"},
    "staging.stg_customers.country": {"raw.customers.country"},
    "staging.stg_items.line_revenue": {"raw.order_items.quantity", "raw.order_items.unit_price"},
    "staging.stg_items.line_cost": {"raw.order_items.quantity", "raw.products.unit_cost"},
    "staging.stg_items.category": {"raw.products.category"},
    "intermediate.int_order_margin.revenue": {"staging.stg_items.line_revenue"},
    "intermediate.int_order_margin.margin": {
        "staging.stg_items.line_revenue",
        "staging.stg_items.line_cost",
    },
    "marts.fct_orders.margin": {"intermediate.int_order_margin.margin"},
    "marts.fct_orders.amount_usd": {"staging.stg_orders.amount_usd"},
    "marts.fct_orders.order_seq": {
        "staging.stg_orders.customer_id",
        "staging.stg_orders.order_date",
    },
    "marts.dim_customer.lifetime_value": {"marts.fct_orders.amount_usd"},
    "marts.dim_customer.orders": {"marts.fct_orders.order_id"},
    "marts.dim_customer.segment": {"staging.stg_customers.segment"},
    "marts.revenue_by_segment.revenue": {"marts.dim_customer.lifetime_value"},
    "marts.category_margin.margin": {
        "staging.stg_items.line_revenue",
        "staging.stg_items.line_cost",
    },
    "marts.exec_dashboard.best_category_margin": {"marts.category_margin.margin"},
    "marts.exec_dashboard.revenue": {"marts.revenue_by_segment.revenue"},
    "marts.email_domains.customers": set(),  # COUNT(*) reads rows, not a column: no column-level source
    "marts.email_domains.email_domain": {"staging.stg_customers.email_domain"},
}
