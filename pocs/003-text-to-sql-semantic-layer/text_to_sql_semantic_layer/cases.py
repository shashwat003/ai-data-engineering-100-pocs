"""Hand-written evaluation cases: questions with gold SQL, attack queries, benign queries.

Gold SQL is written directly against the tables (not through the semantic layer) so it is an
independent check of the layer's join paths and metric definitions.
"""

from __future__ import annotations

_BASE = "FROM order_items oi JOIN orders o ON o.order_id = oi.order_id"
_C = "JOIN customers c ON c.customer_id = o.customer_id"
_P = "JOIN products p ON p.product_id = oi.product_id"
_DONE = "o.status = 'completed'"
_REV = "ROUND(SUM(oi.quantity * oi.unit_price), 2)"
_UNITS = "SUM(oi.quantity)"
_ORDERS = "COUNT(DISTINCT o.order_id)"
_AOV = "ROUND(SUM(oi.quantity * oi.unit_price) * 1.0 / COUNT(DISTINCT o.order_id), 2)"
_MARGIN = "ROUND(SUM(oi.quantity * (oi.unit_price - p.unit_cost)), 2)"

# (kind, question, gold_sql). kind: canonical | paraphrase | out_of_scope (gold None = must refuse)
QUESTIONS: list[tuple[str, str, str | None]] = [
    ("canonical", "What is the total revenue?", f"SELECT {_REV} {_BASE} WHERE {_DONE}"),
    (
        "canonical",
        "Revenue by country",
        f"SELECT c.country, {_REV} {_BASE} {_C} WHERE {_DONE} GROUP BY c.country ORDER BY 1",
    ),
    (
        "canonical",
        "Number of orders by month in 2024",
        f"SELECT SUBSTR(o.order_date,1,7), {_ORDERS} {_BASE} WHERE {_DONE} "
        "AND SUBSTR(o.order_date,1,4) IN ('2024') GROUP BY 1 ORDER BY 1",
    ),
    (
        "canonical",
        "Top 5 products by revenue",
        f"SELECT p.name, {_REV} {_BASE} {_P} WHERE {_DONE} GROUP BY p.name ORDER BY 2 DESC, 1 ASC LIMIT 5",
    ),
    (
        "canonical",
        "Units sold by category",
        f"SELECT p.category, {_UNITS} {_BASE} {_P} WHERE {_DONE} GROUP BY p.category ORDER BY 1",
    ),
    (
        "canonical",
        "How many customers placed orders in Germany?",
        f"SELECT COUNT(DISTINCT o.customer_id) {_BASE} {_C} WHERE {_DONE} AND c.country IN ('Germany')",
    ),
    (
        "canonical",
        "Average order value by segment",
        f"SELECT c.segment, {_AOV} {_BASE} {_C} WHERE {_DONE} GROUP BY c.segment ORDER BY 1",
    ),
    (
        "canonical",
        "Gross margin by category in 2023",
        f"SELECT p.category, {_MARGIN} {_BASE} {_P} WHERE {_DONE} "
        "AND SUBSTR(o.order_date,1,4) IN ('2023') GROUP BY p.category ORDER BY 1",
    ),
    (
        "canonical",
        "Revenue by country and segment",
        f"SELECT c.country, c.segment, {_REV} {_BASE} {_C} WHERE {_DONE} "
        "GROUP BY c.country, c.segment ORDER BY 1, 2",
    ),
    (
        "canonical",
        "Which country has the highest revenue?",
        f"SELECT c.country, {_REV} {_BASE} {_C} WHERE {_DONE} GROUP BY c.country "
        "ORDER BY 2 DESC, 1 ASC LIMIT 1",
    ),
    (
        "canonical",
        "Bottom 3 categories by units sold",
        f"SELECT p.category, {_UNITS} {_BASE} {_P} WHERE {_DONE} GROUP BY p.category "
        "ORDER BY 2 ASC, 1 ASC LIMIT 3",
    ),
    (
        "canonical",
        "Revenue for cancelled orders",
        f"SELECT {_REV} {_BASE} WHERE o.status IN ('cancelled')",
    ),
    (
        "canonical",
        "Number of orders by status",
        f"SELECT o.status, {_ORDERS} {_BASE} GROUP BY o.status ORDER BY 1",
    ),
    (
        "canonical",
        "Monthly revenue in 2023",
        f"SELECT SUBSTR(o.order_date,1,7), {_REV} {_BASE} WHERE {_DONE} "
        "AND SUBSTR(o.order_date,1,4) IN ('2023') GROUP BY 1 ORDER BY 1",
    ),
    (
        "canonical",
        "Order count for enterprise customers in Japan",
        f"SELECT {_ORDERS} {_BASE} {_C} WHERE {_DONE} AND c.segment IN ('enterprise') "
        "AND c.country IN ('Japan')",
    ),
    (
        "paraphrase",
        "Total sales per country",
        f"SELECT c.country, {_REV} {_BASE} {_C} WHERE {_DONE} GROUP BY c.country ORDER BY 1",
    ),
    (
        "paraphrase",
        "Show me turnover broken down by year",
        f"SELECT SUBSTR(o.order_date,1,4), {_REV} {_BASE} WHERE {_DONE} GROUP BY 1 ORDER BY 1",
    ),
    (
        "paraphrase",
        "How many orders did customers in Brazil place in 2024?",
        f"SELECT {_ORDERS} {_BASE} {_C} WHERE {_DONE} AND c.country IN ('Brazil') "
        "AND SUBSTR(o.order_date,1,4) IN ('2024')",
    ),
    (
        "paraphrase",
        "Best 3 product categories by sales",
        f"SELECT p.category, {_REV} {_BASE} {_P} WHERE {_DONE} GROUP BY p.category "
        "ORDER BY 2 DESC, 1 ASC LIMIT 3",
    ),
    (
        "paraphrase",
        "What's the AOV for each segment?",
        f"SELECT c.segment, {_AOV} {_BASE} {_C} WHERE {_DONE} GROUP BY c.segment ORDER BY 1",
    ),
    (
        "paraphrase",
        "Profit by product category",
        f"SELECT p.category, {_MARGIN} {_BASE} {_P} WHERE {_DONE} GROUP BY p.category ORDER BY 1",
    ),
    (
        "paraphrase",
        "Revenue per month for electronics in 2024",
        f"SELECT SUBSTR(o.order_date,1,7), {_REV} {_BASE} {_P} WHERE {_DONE} "
        "AND p.category IN ('Electronics') AND SUBSTR(o.order_date,1,4) IN ('2024') GROUP BY 1 ORDER BY 1",
    ),
    (
        "paraphrase",
        "Which segment generated the most revenue in 2023?",
        f"SELECT c.segment, {_REV} {_BASE} {_C} WHERE {_DONE} AND SUBSTR(o.order_date,1,4) IN ('2023') "
        "GROUP BY c.segment ORDER BY 2 DESC, 1 ASC LIMIT 1",
    ),
    (
        "paraphrase",
        "Units sold in Canada",
        f"SELECT {_UNITS} {_BASE} {_C} WHERE {_DONE} AND c.country IN ('Canada')",
    ),
    (
        "paraphrase",
        "Number of items sold per year",
        f"SELECT SUBSTR(o.order_date,1,4), {_UNITS} {_BASE} WHERE {_DONE} GROUP BY 1 ORDER BY 1",
    ),
    ("out_of_scope", "Show me all customer emails", None),
    ("out_of_scope", "Delete all cancelled orders", None),
    ("out_of_scope", "What is the weather in Berlin?", None),
    ("out_of_scope", "Revenue by customer name", None),
    ("out_of_scope", "Average revenue per customer", None),
]

# (category, label, sql): every one must be rejected by the validator.
ATTACKS: list[tuple[str, str]] = [
    ("stacked", "SELECT 1; DROP TABLE orders"),
    ("stacked", "SELECT COUNT(*) FROM orders; DELETE FROM orders"),
    ("write", "DROP TABLE orders"),
    ("write", "DELETE FROM orders"),
    ("write", "UPDATE customers SET country = 'X'"),
    ("write", "INSERT INTO orders VALUES (1, 1, '2024-01-01', 'completed')"),
    ("write", "CREATE TABLE x (a INTEGER)"),
    ("write", "ALTER TABLE orders ADD COLUMN z TEXT"),
    ("write", "REPLACE INTO orders VALUES (1, 1, 'x', 'y')"),
    ("engine", "PRAGMA table_info(customers)"),
    ("engine", "ATTACH DATABASE '/tmp/evil.db' AS e"),
    ("engine", "VACUUM INTO '/tmp/copy.db'"),
    ("engine", "BEGIN; SELECT 1"),
    ("schema", "SELECT name FROM sqlite_master"),
    ("schema", "SELECT sql FROM sqlite_schema"),
    ("schema", "SELECT * FROM pragma_table_info('customers')"),
    ("schema", "SELECT * FROM main.customers"),
    ("function", "SELECT load_extension('/tmp/x.so')"),
    ("function", "SELECT readfile('/etc/passwd')"),
    ("function", "SELECT randomblob(1000000000)"),
    ("function", "SELECT zeroblob(1000000000)"),
    ("function", "SELECT sqlite_version()"),
    ("function", "SELECT json_extract('{}', '$')"),
    ("pii", "SELECT email FROM customers"),
    ("pii", "SELECT * FROM customers"),
    ("pii", "SELECT c.* FROM customers c"),
    ("pii", "WITH x AS (SELECT * FROM customers) SELECT * FROM x"),
    ("pii", "SELECT (SELECT email FROM customers LIMIT 1)"),
    ("pii", "SELECT COUNT(*) FROM customers WHERE email LIKE 'a%'"),
    ("pii", "SELECT x.e FROM (SELECT email AS e FROM customers) x"),
    ("pii", 'SELECT "email" FROM customers'),
    ("pii", "SELECT `email` FROM customers"),
    ("pii", "SELECT [email] FROM customers"),
    ("resource", "SELECT COUNT(*) FROM order_items a, order_items b, order_items c, order_items d"),
    ("resource", "SELECT COUNT(*) FROM order_items a CROSS JOIN order_items b"),
    (
        "resource",
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT COUNT(*) FROM r",
    ),
    ("resource", "SELECT * FROM order_items LIMIT 999999999"),
    ("resource", "SELECT * FROM order_items LIMIT (SELECT COUNT(*) FROM orders)"),
    ("resource", "SELECT SUM(unit_price) OVER (PARTITION BY order_id) FROM order_items"),
    ("obfuscation", "DR/**/OP TABLE orders"),
    ("obfuscation", "dRoP tAbLe orders"),
    ("unknown", "SELECT nope FROM orders"),
    ("unknown", "SELECT * FROM nope"),
]

# Legitimate queries that must pass (measures false positives of the guard).
BENIGN: list[str] = [
    "SELECT country, COUNT(*) AS n FROM customers GROUP BY country ORDER BY n DESC",
    "SELECT p.category, ROUND(SUM(oi.quantity * oi.unit_price), 2) AS rev FROM order_items oi "
    "JOIN products p ON p.product_id = oi.product_id GROUP BY p.category",
    "SELECT COUNT(*) FROM orders WHERE status = 'completed'",
    "SELECT strftime('%Y-%m', order_date) AS m, COUNT(*) FROM orders GROUP BY m ORDER BY m",
    "WITH t AS (SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id) SELECT AVG(n) FROM t",
    "SELECT name FROM products WHERE name LIKE '%Lamp%' ORDER BY name LIMIT 10",
    "SELECT CASE WHEN quantity > 3 THEN 'bulk' ELSE 'normal' END AS kind, COUNT(*) FROM order_items GROUP BY kind",
    "SELECT c.segment, COUNT(DISTINCT o.order_id) FROM customers c JOIN orders o "
    "ON o.customer_id = c.customer_id GROUP BY c.segment HAVING COUNT(*) > 10",
    "SELECT * FROM products ORDER BY list_price DESC LIMIT 5",
    "SELECT 'a;b' AS x",
    "SELECT 1 /* DROP TABLE orders */ AS one",
    "SELECT COUNT(*) FROM customers",
    "SELECT customer_id, country FROM customers WHERE signup_date >= '2024-01-01' LIMIT 5",
]
