"""Seeded OLTP workload: inserts, updates, deletes, multi-row transactions, money transfers (sum is invariant) and rollbacks."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from decimal import Decimal

SCHEMA = """
CREATE TABLE orders (id int PRIMARY KEY, status text NOT NULL, amount numeric(10,2) NOT NULL, note text);
CREATE TABLE accounts (id int PRIMARY KEY, balance numeric(12,2) NOT NULL);
"""
STATUSES = ("new", "paid", "shipped", "returned", "cancelled")
NOTES = (
    None,
    "",
    "it's fine",
    'say "hi"',
    "line1\nline2",
    "ünïcödé ✓",
    "back\\slash",
    "a, b: c",
    "null",
    "x[integer]:1",
)
N_ACCOUNTS, START_BALANCE = 8, Decimal("1000.00")


@dataclass
class Workload:
    rng: random.Random
    next_id: int = 1
    live: set[int] = field(default_factory=set)
    rolled_back: int = 0
    ghost_ids: set[int] = field(
        default_factory=set
    )  # ids inserted by transactions that were rolled back
    committed: int = 0

    def seed(self, conn, n_orders: int = 60) -> None:
        with conn.transaction():
            conn.execute(SCHEMA)
            for _ in range(n_orders):
                self._insert(conn)
            for i in range(1, N_ACCOUNTS + 1):
                conn.execute("INSERT INTO accounts VALUES (%s, %s)", (i, START_BALANCE))

    def _insert(self, conn) -> None:
        i = self.next_id
        self.next_id += 1
        self.live.add(i)
        conn.execute(
            "INSERT INTO orders VALUES (%s, %s, %s, %s)",
            (
                i,
                self.rng.choice(STATUSES),
                Decimal(self.rng.randint(100, 99999)) / 100,
                self.rng.choice(NOTES),
            ),
        )

    def txn(self, conn) -> str:
        """One random transaction; ~12% are rolled back and must leave no trace downstream."""
        kind = self.rng.choices(
            ["insert", "update", "delete", "transfer", "bulk"], [3, 5, 2, 3, 1]
        )[0]
        rollback = self.rng.random() < 0.12
        first_id = self.next_id
        try:
            with conn.transaction():
                if kind == "insert":
                    for _ in range(self.rng.randint(1, 3)):
                        self._insert(conn)
                elif kind == "update" and self.live:
                    i = self.rng.choice(sorted(self.live))
                    conn.execute("UPDATE orders SET status=%s, amount=%s, note=%s WHERE id=%s",
                                 (self.rng.choice(STATUSES), Decimal(self.rng.randint(100, 99999)) / 100, self.rng.choice(NOTES), i))  # fmt: skip
                elif kind == "delete" and self.live:
                    i = self.rng.choice(sorted(self.live))
                    conn.execute("DELETE FROM orders WHERE id=%s", (i,))
                    self.live.discard(i)
                elif kind == "transfer":
                    a, b = self.rng.sample(range(1, N_ACCOUNTS + 1), 2)
                    amt = Decimal(self.rng.randint(1, 5000)) / 100
                    conn.execute("UPDATE accounts SET balance = balance - %s WHERE id=%s", (amt, a))
                    conn.execute("UPDATE accounts SET balance = balance + %s WHERE id=%s", (amt, b))
                elif kind == "bulk" and self.live:
                    ids = self.rng.sample(sorted(self.live), min(5, len(self.live)))
                    conn.execute("UPDATE orders SET status='returned' WHERE id = ANY(%s)", (ids,))
                if rollback:
                    raise _Rollback
        except _Rollback:
            self.rolled_back += 1
            self.ghost_ids |= set(range(first_id, self.next_id))
            # a rolled-back insert must also disappear from our bookkeeping
            self.live &= {r[0] for r in conn.execute("SELECT id FROM orders").fetchall()}
            return "rolled_back"
        self.committed += 1
        return kind


class _Rollback(Exception):
    pass
