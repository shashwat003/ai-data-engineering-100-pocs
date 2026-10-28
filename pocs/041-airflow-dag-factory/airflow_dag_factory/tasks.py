"""Allow-listed task implementations and shell commands. Config may only reference names defined here."""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path


def extract_orders(work_dir: str, n_rows: int = 100, bad_rows: int = 0, seed: int = 7) -> int:
    rng = random.Random(seed)
    d = Path(work_dir)
    d.mkdir(parents=True, exist_ok=True)
    with (d / "orders.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["order_id", "amount"])
        for i in range(n_rows):
            amount = round(rng.uniform(5, 500), 2)
            w.writerow([i, -1 if i < bad_rows else amount])
    return n_rows


def validate_rows(work_dir: str, max_bad_fraction: float = 0.05) -> float:
    rows = list(csv.DictReader((Path(work_dir) / "orders.csv").open()))
    bad = sum(1 for r in rows if float(r["amount"]) < 0)
    frac = bad / len(rows) if rows else 1.0
    if frac > max_bad_fraction:
        raise ValueError(f"{bad}/{len(rows)} rows invalid ({frac:.1%} > {max_bad_fraction:.1%})")
    return frac


def load_summary(work_dir: str) -> dict:
    rows = list(csv.DictReader((Path(work_dir) / "orders.csv").open()))
    s = {
        "rows": len(rows),
        "revenue": round(sum(float(r["amount"]) for r in rows if float(r["amount"]) >= 0), 2),
    }
    (Path(work_dir) / "summary.json").write_text(json.dumps(s))
    return s


CALLABLES = {
    "extract_orders": extract_orders,
    "validate_rows": validate_rows,
    "load_summary": load_summary,
}
COMMANDS = {"echo_done": "echo pipeline finished", "show_date": "date -u +%Y-%m-%d"}
