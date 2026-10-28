"""Synthetic tables with labelled PII columns and look-alike traps."""

from __future__ import annotations

import random

import duckdb
from agent_policy_guardrails.data import iban_de, luhn_number

FIRST = ["Ana", "Ben", "Chen", "Dara", "Eli", "Fay", "Gus", "Hana"]
LAST = ["Ng", "Okoro", "Patel", "Quinn", "Reyes", "Sato", "Tran", "Usman"]
# column -> expected class (None = not PII)
TRUTH = {
    ("customers", "customer_id"): None, ("customers", "full_name"): "person_name", ("customers", "email"): "email", ("customers", "phone_number"): "phone",
    ("customers", "dob"): "dob", ("customers", "signup_date"): None, ("customers", "tier"): None,
    ("payments", "payment_id"): None, ("payments", "card_no"): "credit_card", ("payments", "payer_iban"): "iban", ("payments", "amount"): None, ("payments", "order_ref"): None,
    ("hr", "emp_id"): None, ("hr", "ssn"): "us_ssn", ("hr", "contact"): "email", ("hr", "salary"): None,
    ("misc", "col_a"): "email", ("misc", "legacy_phone"): None, ("misc", "account_number"): None,
    ("tickets", "ticket_id"): None, ("tickets", "body"): None, ("tickets", "product"): None, ("tickets", "tracking_number"): None,
}  # fmt: skip


def build(n: int = 800, seed: int = 4) -> duckdb.DuckDBPyConnection:
    rng = random.Random(seed)
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE customers (customer_id INTEGER, full_name VARCHAR, email VARCHAR, phone_number VARCHAR, dob VARCHAR, signup_date VARCHAR, tier VARCHAR)"
    )
    con.execute(
        "CREATE TABLE payments (payment_id INTEGER, card_no VARCHAR, payer_iban VARCHAR, amount DOUBLE, order_ref VARCHAR)"
    )
    con.execute("CREATE TABLE hr (emp_id INTEGER, ssn VARCHAR, contact VARCHAR, salary INTEGER)")
    con.execute("CREATE TABLE misc (col_a VARCHAR, legacy_phone VARCHAR, account_number VARCHAR)")
    con.execute(
        "CREATE TABLE tickets (ticket_id INTEGER, body VARCHAR, product VARCHAR, tracking_number VARCHAR)"
    )
    for i in range(n):
        con.execute(
            "INSERT INTO customers VALUES (?,?,?,?,?,?,?)",
            [
                i,
                f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                f"user{i}@example.com" if rng.random() > 0.05 else None,
                f"+1 555 {rng.randint(100, 999)} {rng.randint(1000, 9999)}",
                f"19{rng.randint(50, 99)}-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}",
                f"2024-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}",
                rng.choice(["free", "pro"]),
            ],
        )
        card = luhn_number(rng)
        con.execute(
            "INSERT INTO payments VALUES (?,?,?,?,?)",
            [
                i,
                card,
                iban_de(rng),
                round(rng.uniform(5, 500), 2),
                f"{rng.randint(10**9, 10**10 - 1)}",
            ],
        )
        con.execute(
            "INSERT INTO hr VALUES (?,?,?,?)",
            [
                i,
                f"{rng.randint(100, 665)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}",
                f"emp{i}@corp.example",
                rng.randint(40000, 150000),
            ],
        )
        con.execute(
            "INSERT INTO misc VALUES (?,?,?)",
            [
                f"anon{i}@vendor.example",
                f"{rng.getrandbits(64):016x}",
                str(rng.randint(10**15, 10**16 - 1)),
            ],
        )
        body = rng.choice(
            ["Printer jams on page two.", "Cannot log in since update.", "Refund has not arrived."]
        )
        if rng.random() < 0.1:
            body += f" Contact me at person{i}@mail.example or +1 555 {rng.randint(100, 999)} {rng.randint(1000, 9999)}."
        con.execute(
            "INSERT INTO tickets VALUES (?,?,?,?)",
            [i, body, rng.choice(["A", "B"]), f"1Z{rng.randint(10**9, 10**10 - 1)}"],
        )
    return con
