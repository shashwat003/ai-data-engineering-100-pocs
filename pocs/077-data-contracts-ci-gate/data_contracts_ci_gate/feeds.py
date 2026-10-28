"""Synthetic order feeds (clean and defective) derived from the shared demo warehouse.

Defects are injected on disjoint rows with known counts, so tests can check that the validator
finds exactly what was injected, no more and no fewer.
"""

from __future__ import annotations

import csv
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from data_gen.warehouse import build_warehouse

COLUMNS = ["order_id", "customer_id", "order_date", "ship_date", "status", "amount", "email"]
DEFECTS = {
    "null_customer_id": 5,
    "negative_amount": 4,
    "bad_status": 6,
    "duplicate_order_id": 3,
    "bad_email": 4,
    "ship_before_order": 3,
    "non_numeric_amount": 2,
    "amount_too_large": 2,
}
QUERY = """
SELECT o.order_id, o.customer_id, o.order_date, o.status,
       ROUND(SUM(oi.quantity * oi.unit_price), 2) AS amount, c.email
FROM orders o JOIN order_items oi ON oi.order_id = o.order_id
JOIN customers c ON c.customer_id = o.customer_id
GROUP BY o.order_id ORDER BY o.order_id LIMIT ?
"""


def _write(path: Path, rows: list[dict], columns: list[str] = COLUMNS) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_feeds(out_dir: str | Path, *, seed: int = 5, n_rows: int = 400) -> dict:
    """Write feed files into out_dir and return a manifest of what was injected."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    db = out / "warehouse.db"
    build_warehouse(db, seed=seed)
    con = sqlite3.connect(db)
    try:
        raw = con.execute(QUERY, (n_rows,)).fetchall()
    finally:
        con.close()
    rng = random.Random(seed)
    clean = []
    for oid, cid, odate, status, amount, email in raw:
        shipped = status in ("completed", "shipped", "returned")
        ship = (
            (date.fromisoformat(odate) + timedelta(days=rng.randint(1, 5))).isoformat()
            if shipped
            else ""
        )
        clean.append({"order_id": oid, "customer_id": cid, "order_date": odate, "ship_date": ship,
                      "status": status, "amount": amount, "email": email})  # fmt: skip
    _write(out / "orders_clean.csv", clean)

    defective = [dict(r) for r in clean]
    while True:  # duplicates copy the previous row's id, so none may be adjacent to another
        pool = rng.sample(range(1, len(defective)), sum(DEFECTS.values()))
        picks, start = {}, 0
        for name, count in DEFECTS.items():
            picks[name] = pool[start : start + count]
            start += count
        dups = sorted(picks["duplicate_order_id"])
        if all(b - a > 1 for a, b in zip(dups, dups[1:], strict=False)) and not set(
            i - 1 for i in dups
        ) & set(dups):
            break
    # ship_before_order needs a shipped row; swap in rows that have a ship_date
    shippable = [i for i, r in enumerate(defective) if r["ship_date"] and i not in pool]
    picks["ship_before_order"] = rng.sample(shippable, DEFECTS["ship_before_order"])
    for i in picks["null_customer_id"]:
        defective[i]["customer_id"] = ""
    for i in picks["negative_amount"]:
        defective[i]["amount"] = -abs(defective[i]["amount"]) - 1
    for i in picks["bad_status"]:
        defective[i]["status"] = "pending"
    for i in picks["duplicate_order_id"]:
        defective[i]["order_id"] = clean[i - 1]["order_id"]
    for i in picks["bad_email"]:
        defective[i]["email"] = "not-an-email"
    for i in picks["ship_before_order"]:
        defective[i]["ship_date"] = (
            date.fromisoformat(defective[i]["order_date"]) - timedelta(days=2)
        ).isoformat()
    for i in picks["non_numeric_amount"]:
        defective[i]["amount"] = "12,50"
    for i in picks["amount_too_large"]:
        defective[i]["amount"] = 10_000_000
    _write(out / "orders_defective.csv", defective)

    _write(out / "orders_missing_column.csv", clean, [c for c in COLUMNS if c != "amount"])
    _write(
        out / "orders_extra_column.csv", [{**r, "coupon": "X"} for r in clean], [*COLUMNS, "coupon"]
    )
    newest = max(date.fromisoformat(r["order_date"]) for r in clean)
    stale = [r for r in clean if date.fromisoformat(r["order_date"]) <= newest - timedelta(days=60)]
    _write(out / "orders_stale.csv", stale)
    return {
        "rows": len(clean),
        "injected": DEFECTS,
        "newest_order_date": newest.isoformat(),
        "stale_rows": len(stale),
    }
