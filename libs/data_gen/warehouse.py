"""Seeded synthetic retail warehouse written to a SQLite file (no real or personal data).

Tables: customers, products, orders, order_items. Emails are fabricated on example.com.
Used by POC 003 (text-to-SQL), POC 024 (data catalogue) and POC 077 (contract feeds).
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

COUNTRIES = (
    "United States",
    "Germany",
    "India",
    "Brazil",
    "Japan",
    "United Kingdom",
    "Canada",
    "Australia",
)
SEGMENTS = ("consumer", "smb", "enterprise")
CATEGORIES = ("Electronics", "Home", "Books", "Toys", "Outdoors")
STATUSES = ("completed", "shipped", "cancelled", "returned")
FIRST = "Ava Ben Chen Dara Eli Fay Gus Hana Ivan Jia Kai Lena Milo Nia Omar Pia Raj Sara Tom Uma".split()
LAST = (
    "Adams Brown Clark Diaz Evans Frost Garcia Hill Ito Jones Khan Lopez Meyer Nguyen Okoye".split()
)
ADJECTIVES = "Compact Deluxe Eco Smart Classic Rugged Slim Pro".split()
NOUNS = "Lamp Kettle Backpack Speaker Puzzle Novel Tent Blender Mouse Drone".split()

SCHEMA = """
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    country TEXT NOT NULL,
    segment TEXT NOT NULL,
    signup_date TEXT NOT NULL
);
CREATE TABLE products (
    product_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit_cost REAL NOT NULL,
    list_price REAL NOT NULL
);
CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE order_items (
    order_item_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id),
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL
);
"""


def build_warehouse(
    path: str | Path, *, seed: int = 3, n_customers: int = 200, n_orders: int = 1500
) -> dict[str, int]:
    """Create (replace) the SQLite file at `path`; return row counts per table."""
    rng = random.Random(seed)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)

    start = date(2023, 1, 1)
    customers = [
        (
            i,
            (name := f"{rng.choice(FIRST)} {rng.choice(LAST)}"),
            f"{name.lower().replace(' ', '.')}.{i}@example.com",
            rng.choice(COUNTRIES),
            rng.choices(SEGMENTS, weights=(6, 3, 1))[0],
            (start + timedelta(days=rng.randrange(0, 700))).isoformat(),
        )
        for i in range(1, n_customers + 1)
    ]
    products = []
    for i in range(1, 41):
        cost = round(rng.uniform(3, 120), 2)
        products.append(
            (
                i,
                f"{rng.choice(ADJECTIVES)} {rng.choice(NOUNS)} {i}",
                CATEGORIES[(i - 1) % len(CATEGORIES)],
                cost,
                round(cost * rng.uniform(1.3, 2.2), 2),
            )
        )
    orders, items = [], []
    for oid in range(1, n_orders + 1):
        placed = start + timedelta(days=rng.randrange(0, 730))
        status = rng.choices(STATUSES, weights=(80, 8, 7, 5))[0]
        orders.append((oid, rng.randint(1, n_customers), placed.isoformat(), status))
        for _ in range(rng.randint(1, 4)):
            prod = rng.choice(products)
            price = round(prod[4] * rng.uniform(0.9, 1.0), 2)
            items.append((len(items) + 1, oid, prod[0], rng.randint(1, 5), price))

    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?)", customers)
        con.executemany("INSERT INTO products VALUES (?,?,?,?,?)", products)
        con.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)
        con.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", items)
        con.commit()
    finally:
        con.close()
    return {
        "customers": len(customers),
        "products": len(products),
        "orders": len(orders),
        "order_items": len(items),
    }
