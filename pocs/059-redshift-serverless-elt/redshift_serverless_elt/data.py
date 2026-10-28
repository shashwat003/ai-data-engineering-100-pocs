"""Seeded synthetic landing files (CSV, as an S3 prefix would hold them) with exactly-known defects."""

from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

SEGMENTS = ("consumer", "smb", "enterprise")
COUNTRIES = ("US", "DE", "IN", "BR", "JP", "GB")
STATUSES = ("completed", "shipped", "cancelled", "returned")
N_CUSTOMERS, N_BATCHES, ORDERS_PER_BATCH = 150, 4, 250
# defects per batch: COPY rejects (bad amount text), SQL rejects (bad status / negative amount), late updates re-sent
DEFECTS = {2: {"copy_bad_amount": 3, "bad_status": 2, "negative_amount": 2}, 4: {"bad_status": 1}}
RESENT = 10
LATE_NEW = {3: 6}  # first-time orders that arrive in batch 3 but are dated inside batch 1's window
WINDOW_DAYS = 30


def generate(landing: str | Path, seed: int = 5) -> dict:
    landing = Path(landing)
    landing.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    cust = {c: [rng.choice(SEGMENTS), rng.choice(COUNTRIES)] for c in range(1, N_CUSTOMERS + 1)}
    day0, oid, seen = date(2024, 1, 1), 1, []
    manifest = {
        "batches": [],
        "expected": {"copy_rejects": 0, "sql_rejects": 0, "late_updates": 0, "late_new_orders": 0},
        "effective": {},
    }
    for b in range(1, N_BATCHES + 1):
        bid = f"b{b:03d}"
        d = landing / bid
        d.mkdir(exist_ok=True)
        rows = []
        for _ in range(ORDERS_PER_BATCH):
            od = day0 + timedelta(days=(b - 1) * WINDOW_DAYS + rng.randrange(WINDOW_DAYS))
            rows.append({"order_id": oid, "customer_id": rng.randint(1, N_CUSTOMERS), "order_date": od.isoformat(),
                         "status": rng.choices(STATUSES, [70, 15, 8, 7])[0], "amount": f"{rng.uniform(5, 900):.2f}"})  # fmt: skip
            oid += 1
        late = []
        if seen:
            for r in rng.sample(seen, RESENT):
                new = dict(r)
                new["status"] = rng.choice(
                    [s for s in ("completed", "returned") if s != r["status"]]
                )
                late.append(new)
            manifest["expected"]["late_updates"] += RESENT
        rows += late
        for _ in range(LATE_NEW.get(b, 0)):
            od = day0 + timedelta(
                days=rng.randrange(WINDOW_DAYS)
            )  # belongs to window 1, arrives now
            rows.append({"order_id": oid, "customer_id": rng.randint(1, N_CUSTOMERS), "order_date": od.isoformat(), "status": "completed", "amount": f"{rng.uniform(5, 900):.2f}"})  # fmt: skip
            oid += 1
            manifest["expected"]["late_new_orders"] += 1
        clean = {r["order_id"]: r for r in rows}  # latest wins inside the batch
        seen += [dict(r) for r in rows if r not in late]
        dfx = DEFECTS.get(b, {})
        fresh = [r for r in rows if r not in late]
        for kind, n in dfx.items():
            for r in rng.sample(fresh, n):
                if kind == "copy_bad_amount":
                    r["amount"] = "12,50"
                elif kind == "bad_status":
                    r["status"] = "teleported"
                elif kind == "negative_amount":
                    r["amount"] = "-5.00"
        manifest["expected"]["copy_rejects"] += dfx.get("copy_bad_amount", 0)
        manifest["expected"]["sql_rejects"] += dfx.get("bad_status", 0) + dfx.get(
            "negative_amount", 0
        )
        del clean
        with (d / "orders.csv").open("w", newline="") as fh:
            w = csv.DictWriter(
                fh, fieldnames=["order_id", "customer_id", "order_date", "status", "amount"]
            )
            w.writeheader()
            w.writerows(rows)
        # customers: segment changes over time for a few customers
        if b > 1:
            for c in rng.sample(sorted(cust), 6):
                cust[c][0] = rng.choice([s for s in SEGMENTS if s != cust[c][0]])
        with (d / "customers.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["customer_id", "segment", "country"])
            w.writerows([[c, *v] for c, v in sorted(cust.items())])
        eff = (
            day0 + timedelta(days=(b - 1) * WINDOW_DAYS)
        ).isoformat()  # the customer snapshot describes this window onwards
        (d / "_meta.json").write_text(json.dumps({"batch_id": bid, "effective_date": eff}))
        manifest["effective"][bid] = eff
        manifest["batches"].append({"batch": bid, "orders": len(rows)})
    return manifest
