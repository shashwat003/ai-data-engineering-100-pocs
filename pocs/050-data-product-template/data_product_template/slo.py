"""Evaluate a data product's SLOs on its DuckDB output table plus a load log (`_loads`).

SLIs: freshness (hours since last successful load), completeness (non-null share of required cells),
validity (share of rows free of contract errors, via POC 077's validator), volume (latest load vs the
median of prior loads) and availability (successful loads / loads in the window) with an error budget.
"""

from __future__ import annotations

import csv
import statistics
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
from data_contracts_ci_gate.contract import Contract
from data_contracts_ci_gate.validate import validate_dataset

from .product import Product

LOG_DDL = "create table if not exists _loads(load_ts timestamp, ok boolean, rows bigint)"


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True)
class Sli:
    name: str
    value: float
    target: float
    met: bool
    detail: str


def evaluate(
    con: duckdb.DuckDBPyConnection, product: Product, contract: Contract, now: datetime
) -> dict:
    s, t = product.slos, q(product.output_table)
    log = con.execute(
        "select load_ts, ok, rows from _loads where load_ts <= ? order by load_ts", [now]
    ).fetchall()
    window = [r for r in log if r[0] >= now - timedelta(days=s.window_days)]
    ok_loads = [r for r in log if r[1]]
    slis: list[Sli] = []

    hours = (now - ok_loads[-1][0]).total_seconds() / 3600 if ok_loads else float("inf")
    slis.append(
        Sli(
            "freshness_hours",
            round(hours, 2),
            s.freshness_hours,
            hours <= s.freshness_hours,
            "hours since last successful load",
        )
    )

    n = con.execute(f"select count(*) from {t}").fetchone()[0]  # noqa: S608
    req = [c.name for c in contract.columns if c.required]
    if n and req:
        nn = sum(con.execute(f"select count({q(c)}) from {t}").fetchone()[0] for c in req)  # noqa: S608
        comp = nn / (n * len(req))
    else:
        comp = 0.0
    slis.append(
        Sli(
            "completeness",
            round(comp, 4),
            s.completeness_min,
            comp >= s.completeness_min,
            "non-null share of required cells",
        )
    )

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "out.csv"
        cols = [c.name for c in contract.columns]
        cur = con.execute(f"select {', '.join(q(c) for c in cols)} from {t}")  # noqa: S608
        with p.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            w.writerows(cur.fetchall())
        rep = validate_dataset(contract, p, as_of=now.date())
    bad = sum(v.count for v in rep.errors if v.rule not in ("min_rows", "max_rows", "freshness"))
    validity = 1 - min(1.0, bad / n) if n else 0.0
    slis.append(
        Sli(
            "validity",
            round(validity, 4),
            s.validity_min,
            validity >= s.validity_min,
            f"{len(rep.errors)} contract error rule(s) violated",
        )
    )

    prior = [r[2] for r in ok_loads[:-1]]
    if ok_loads and prior:
        base = statistics.median(prior)
        dev = abs(ok_loads[-1][2] - base) / base * 100 if base else 100.0
    else:
        dev = 0.0 if ok_loads else 100.0
    slis.append(
        Sli(
            "volume_deviation_pct",
            round(dev, 1),
            s.volume_tolerance_pct,
            dev <= s.volume_tolerance_pct,
            "latest load vs median of prior loads",
        )
    )

    total = len(window)
    avail = sum(1 for r in window if r[1]) / total if total else 0.0
    budget = (1 - s.availability_target) * total
    failed = total - sum(1 for r in window if r[1])
    slis.append(
        Sli(
            "availability",
            round(avail, 4),
            s.availability_target,
            avail >= s.availability_target,
            f"{failed} failed of {total} loads in {s.window_days}d",
        )
    )
    return {
        "product": product.name,
        "as_of": now.isoformat(timespec="minutes"),
        "slis": [s_.__dict__ for s_ in slis],
        "all_met": all(x.met for x in slis),
        "error_budget": {
            "allowed_failures": round(budget, 2),
            "failures": failed,
            "remaining": round(budget - failed, 2),
            "exhausted": failed > budget,
        },
    }
