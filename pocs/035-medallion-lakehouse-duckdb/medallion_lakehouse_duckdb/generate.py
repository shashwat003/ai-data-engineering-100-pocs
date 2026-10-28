"""Synthetic landing zone: daily batches of orders and customer snapshots, with known defects.

Orders come from POC 077's clean feed (itself derived from the seeded demo warehouse). The feed is
cut into batches by order date. Later batches re-send a few earlier orders with a new status
(late updates) and customers change segment between snapshots (SCD2 input). Defects are injected
on disjoint rows with exact counts, so tests can assert what quarantine must contain.
"""

from __future__ import annotations

import csv
import json
import random
import tempfile
from datetime import date, timedelta
from pathlib import Path

from data_contracts_ci_gate.feeds import COLUMNS, build_feeds

SEGMENTS = ("consumer", "smb", "enterprise")
COUNTRIES = ("United States", "Germany", "India", "Brazil", "Japan", "United Kingdom")
# batch index (1-based) -> {quarantine reason: count injected}
INJECTED = {
    2: {
        "required:customer_id": 2,
        "range:amount_min": 2,
        "enum:status": 3,
        "unique:order_id": 2,
        "pattern:email": 2,
        "rule:ship_before_order": 2,
        "type:amount": 1,
        "range:amount_max": 1,
    },
    4: {"enum:status": 2, "range:amount_min": 1},
}
N_BATCHES = 4
RESENT_PER_BATCH = 8
CHANGES_PER_BATCH = 6


def _write(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)


def _inject(rows: list[dict], counts: dict[str, int], rng: random.Random) -> list[dict]:
    """Corrupt disjoint rows (or append duplicates) so each defect type appears exactly n times."""
    rows = [dict(r) for r in rows]
    free = list(range(len(rows)))
    rng.shuffle(free)

    def take(n: int, need_ship: bool = False) -> list[int]:
        picked = [i for i in free if not need_ship or rows[i]["ship_date"]][:n]
        for i in picked:
            free.remove(i)
        return picked

    for i in take(counts.get("required:customer_id", 0)):
        rows[i]["customer_id"] = ""
    for i in take(counts.get("range:amount_min", 0)):
        rows[i]["amount"] = f"-{float(rows[i]['amount']) + 1:.2f}"
    for i in take(counts.get("enum:status", 0)):
        rows[i]["status"] = "pending"
    for i in take(counts.get("pattern:email", 0)):
        rows[i]["email"] = "not-an-email"
    for i in take(counts.get("rule:ship_before_order", 0), need_ship=True):
        rows[i]["ship_date"] = (
            date.fromisoformat(rows[i]["order_date"]) - timedelta(days=2)
        ).isoformat()
    for i in take(counts.get("type:amount", 0)):
        rows[i]["amount"] = "12,50"
    for i in take(counts.get("range:amount_max", 0)):
        rows[i]["amount"] = "10000000"
    for i in take(counts.get("unique:order_id", 0)):
        rows.append(dict(rows[i]))  # exact repeat of a row in the same batch, arrives last
    return rows


def generate_landing(landing: str | Path, *, seed: int = 11, n_orders: int = 600) -> dict:
    """Write landing/<batch_id>/{orders.csv,customers.csv,_meta.json}; return a manifest."""
    landing = Path(landing)
    landing.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    with tempfile.TemporaryDirectory() as tmp:
        build_feeds(tmp, seed=seed, n_rows=n_orders)
        with (Path(tmp) / "orders_clean.csv").open(newline="", encoding="utf-8") as fh:
            clean = sorted(csv.DictReader(fh), key=lambda r: (r["order_date"], int(r["order_id"])))

    size = len(clean) // N_BATCHES
    chunks = [clean[k * size : (k + 1) * size if k < N_BATCHES - 1 else None] for k in range(4)]
    first_chunk: dict[str, int] = {}
    for k, chunk in enumerate(chunks):
        for r in chunk:
            first_chunk.setdefault(r["customer_id"], k)
    profile = {c: (rng.choice(SEGMENTS), rng.choice(COUNTRIES)) for c in first_chunk}

    manifest: dict = {"batches": [], "injected": {}, "resent": {}, "customer_changes": {}}
    seen_orders: list[dict] = []
    for k, chunk in enumerate(chunks, start=1):
        batch_id = f"b{k:03d}"
        eff = date.fromisoformat(chunk[-1]["order_date"]) + timedelta(days=1)
        rows = [dict(r) for r in chunk]
        resent = []
        if seen_orders:  # late updates: same order id, new status, resent in a later batch
            for r in rng.sample(seen_orders, RESENT_PER_BATCH):
                new = dict(r)
                new["status"] = rng.choice(
                    [s for s in ("completed", "returned") if s != r["status"]]
                )
                resent.append(new["order_id"])
                rows.append(new)
        if k in INJECTED:
            rows = _inject(rows, INJECTED[k], rng)
        seen_orders += [dict(r) for r in chunk]
        manifest["resent"][batch_id] = sorted(resent, key=int)
        manifest["injected"][batch_id] = INJECTED.get(k, {})

        changed: list[str] = []
        if k > 1:
            pool = sorted((c for c, f in first_chunk.items() if f < k - 1), key=int)
            for c in rng.sample(pool, CHANGES_PER_BATCH):
                seg, ctry = profile[c]
                profile[c] = (rng.choice([s for s in SEGMENTS if s != seg]), ctry)
                changed.append(c)
        manifest["customer_changes"][batch_id] = sorted(changed, key=int)
        customers = [
            {"customer_id": c, "segment": s, "country": ctry}
            for c, (s, ctry) in sorted(profile.items(), key=lambda kv: int(kv[0]))
            if first_chunk[c] <= k - 1
        ]
        d = landing / batch_id
        d.mkdir(exist_ok=True)
        _write(d / "orders.csv", rows, COLUMNS)
        _write(d / "customers.csv", customers, ["customer_id", "segment", "country"])
        meta = {"batch_id": batch_id, "effective_date": eff.isoformat()}
        (d / "_meta.json").write_text(json.dumps(meta))
        manifest["batches"].append({**meta, "orders": len(rows), "customers": len(customers)})
    manifest["newest_order_date"] = clean[-1]["order_date"]
    return manifest
