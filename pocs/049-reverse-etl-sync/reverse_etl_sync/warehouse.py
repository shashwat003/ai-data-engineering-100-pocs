"""Warehouse source: a customers_360 table in DuckDB with a seeded history of changes."""

from __future__ import annotations

import duckdb
import numpy as np

SEGMENTS = ["consumer", "smb", "enterprise"]


def build(path: str, n: int = 1000, seed: int = 4) -> duckdb.DuckDBPyConnection:
    rng = np.random.default_rng(seed)
    con = duckdb.connect(path)
    con.execute(
        "CREATE OR REPLACE TABLE customers_360 (customer_id VARCHAR, email VARCHAR, first_name VARCHAR, last_name VARCHAR, lifetime_value DOUBLE, churn_risk DOUBLE, segment VARCHAR, internal_note VARCHAR, ssn_last4 VARCHAR)"
    )
    rows = [
        (
            f"c{i:05d}",
            f"user{i}@example.com",
            f"First{i % 90}",
            f"Last{i % 70}",
            round(float(rng.gamma(2, 200)), 2),
            round(float(rng.beta(2, 8)), 3),
            SEGMENTS[i % 3],
            "do not share",
            f"{rng.integers(1000, 9999)}",
        )
        for i in range(n)
    ]
    con.executemany("INSERT INTO customers_360 VALUES (?,?,?,?,?,?,?,?,?)", rows)
    return con


def apply_changes(
    con: duckdb.DuckDBPyConnection,
    frac_update: float = 0.05,
    n_delete: int = 10,
    n_new: int = 20,
    seed: int = 8,
    bad_email_ids: tuple[str, ...] = (),
) -> dict:
    """Mutate the warehouse the way a day of business would."""
    rng = np.random.default_rng(seed)
    ids = [r[0] for r in con.execute("SELECT customer_id FROM customers_360 ORDER BY 1").fetchall()]
    upd = list(rng.choice(ids, int(len(ids) * frac_update), replace=False))
    for cid in upd:
        con.execute(
            "UPDATE customers_360 SET churn_risk = round(least(1.0, churn_risk + 0.05), 3), lifetime_value = round(lifetime_value * 1.1, 2) WHERE customer_id = ?",
            [cid],
        )
    dele = ids[len(ids) - n_delete :]  # not ids[-n:], which is the whole list when n == 0
    if dele:
        con.execute(
            f"DELETE FROM customers_360 WHERE customer_id IN ({','.join('?' for _ in dele)})", dele
        )
    base = len(ids)
    for j in range(n_new):
        con.execute(
            "INSERT INTO customers_360 VALUES (?,?,?,?,?,?,?,?,?)",
            [
                f"c{base + j:05d}",
                f"new{j}@example.com",
                "New",
                f"Person{j}",
                100.0,
                0.2,
                "consumer",
                "do not share",
                "1234",
            ],
        )
    for cid in bad_email_ids:
        con.execute("UPDATE customers_360 SET email = 'not-an-email' WHERE customer_id = ?", [cid])
    return {"updated": [str(x) for x in upd], "deleted": dele, "new": n_new}
