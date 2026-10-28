"""Scenario helpers shared by the demo and the tests."""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import duckdb

from . import data, pipeline
from .loader import Warehouse


def staged(landing: Path, upto: int, dest: Path) -> Path:
    """A landing prefix holding batches 1..upto (simulates files arriving over time)."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    for d in sorted(p for p in landing.iterdir() if p.is_dir())[:upto]:
        shutil.copytree(d, dest / d.name)
    return dest


def table_hash(db: Path, sql: str) -> str:
    con = duckdb.connect(str(db))
    rows = con.execute(sql).fetchall()
    con.close()
    import hashlib

    return hashlib.sha256(repr(rows).encode()).hexdigest()[:16]


FCT = "SELECT order_id, customer_id, order_date, status, amount, segment, country FROM fct_orders ORDER BY order_id"
LATEST_VALID = """SELECT order_id, customer_id, order_date, status, amount FROM (
    SELECT order_id, arg_max(customer_id, (_batch_id, _loaded_at)) customer_id, arg_max(order_date, (_batch_id, _loaded_at)) order_date,
           arg_max(status, (_batch_id, _loaded_at)) status, arg_max(amount, (_batch_id, _loaded_at)) amount
    FROM raw.orders GROUP BY order_id)
    WHERE status IN ('completed','shipped','cancelled','returned') AND amount >= 0 AND customer_id IS NOT NULL"""
SNAPS = """SELECT customer_id, _effective_date AS eff, arg_max(segment, _loaded_at) AS segment, arg_max(country, _loaded_at) AS country
           FROM raw.customers WHERE _effective_date IS NOT NULL GROUP BY 1, 2"""
# independent point-in-time attribution: DuckDB ASOF join (latest snapshot on or before the order date), falling back to the earliest snapshot
ASOF = f"""WITH snaps AS ({SNAPS}), first AS (SELECT customer_id, arg_min(segment, eff) AS segment FROM snaps GROUP BY 1)
    SELECT o.order_id, o.amount, o.status, coalesce(s.segment, f.segment) AS segment
    FROM ({LATEST_VALID}) o ASOF LEFT JOIN snaps s ON o.customer_id = s.customer_id AND o.order_date >= s.eff
    LEFT JOIN first f ON f.customer_id = o.customer_id"""
NET = "CASE WHEN status IN ('completed','shipped') THEN amount ELSE 0 END"


def build(work: Path, db: Path, extra: list[str] | None = None) -> dict:
    return pipeline.dbt(["build"], work, db, extra=extra)


def reconcile(db: Path) -> dict:
    """Independent recomputation from the raw layer (different SQL, and DuckDB's ASOF join instead of range joins)."""
    con = duckdb.connect(str(db))
    q = lambda sql: con.execute(sql).fetchall()  # noqa: E731
    expected = dict(q(f"SELECT order_id, segment FROM ({ASOF})"))
    actual = dict(q("SELECT order_id, segment FROM fct_orders"))
    mismatch = sum(1 for k, v in expected.items() if actual.get(k) != v)
    seg_exp = dict(q(f"SELECT segment, round(sum({NET}), 2) FROM ({ASOF}) GROUP BY 1"))
    seg_act = dict(q("SELECT segment, round(net_revenue, 2) FROM revenue_by_segment"))
    cur_exp = dict(
        q(f"""WITH snaps AS ({SNAPS}), cur AS (SELECT customer_id, arg_max(segment, eff) segment FROM snaps GROUP BY 1)
        SELECT c.segment, round(sum({NET}), 2) FROM ({LATEST_VALID}) o JOIN cur c USING (customer_id) GROUP BY 1""")
    )
    cur_act = dict(q("SELECT segment, round(net_revenue, 2) FROM revenue_by_current_segment"))
    out = {
        "valid_orders_expected": len(expected), "fct_orders": len(actual), "orders_with_wrong_point_in_time_segment": mismatch,
        "segment_revenue_matches": seg_exp == seg_act, "current_segment_revenue_matches": cur_exp == cur_act,
        "rejected_expected": q(f"SELECT COUNT(*) FROM (SELECT order_id FROM raw.orders GROUP BY order_id) WHERE order_id NOT IN (SELECT order_id FROM ({LATEST_VALID}))")[0][0],
        "rejected_orders": q("SELECT COUNT(*) FROM rejected_orders")[0][0],
        "segments_differ_between_rules": seg_act != cur_act,
    }  # fmt: skip
    con.close()
    out["ok"] = (
        out["valid_orders_expected"] == out["fct_orders"] and mismatch == 0 and out["segment_revenue_matches"]
        and out["current_segment_revenue_matches"] and out["rejected_expected"] == out["rejected_orders"]
    )  # fmt: skip
    return out


def _load(w: Warehouse, landing: Path, entity: str, batch: str) -> None:
    meta = json.loads((landing / batch / "_meta.json").read_text())
    w.copy(
        entity,
        landing / batch / f"{entity}.csv",
        batch,
        maxerror=5,
        effective_date=meta["effective_date"],
    )


def _arrival(order: str, rng: random.Random) -> list[list[tuple[str, str]]]:
    """Groups of file loads; a build follows each group. All 8 files (4 batches x orders+customers) always end up loaded."""
    b = ["b001", "b002", "b003", "b004"]
    if order == "in order":
        return [[("customers", x), ("orders", x)] for x in b]
    if order == "customers one batch late":
        g = [[("orders", "b001")]]
        for i in range(1, 4):
            g.append([("customers", b[i - 1]), ("orders", b[i])])
        g.append([("customers", "b004")])
        return g
    if order == "batches reversed":
        return [[("orders", x), ("customers", x)] for x in reversed(b)]
    files = [(e, x) for x in b for e in ("customers", "orders")]
    rng.shuffle(files)
    cuts = sorted(rng.sample(range(1, len(files)), 3))
    return [files[i:j] for i, j in zip([0, *cuts], [*cuts, len(files)], strict=True)]


ARRIVALS = ("in order", "customers one batch late", "batches reversed", "random interleaving")


def arrival_scenario(work: Path, landing: Path, order: str, seed: int = 0) -> dict:
    """Load files in the given arrival pattern with a dbt build after each group; compare with one full refresh."""
    work = work / order.replace(" ", "_")
    work.mkdir(parents=True, exist_ok=True)
    inc = work / "inc.duckdb"
    builds = []
    for group in _arrival(order, random.Random(seed)):
        w = Warehouse(inc)
        for entity, batch in group:
            _load(w, landing, entity, batch)
        w.close()
        builds.append(
            build(work / "inc", inc)["success"]
        )  # a failing build is the data-quality gate working (e.g. orders before their customers)
    full = work / "full.duckdb"
    w = Warehouse(full)
    for batch in ("b001", "b002", "b003", "b004"):
        for entity in ("customers", "orders"):
            _load(w, landing, entity, batch)
    w.close()
    build(work / "full", full)
    h_inc, h_full = table_hash(inc, FCT), table_hash(full, FCT)
    rec = reconcile(inc)
    return {"arrival": order, "builds": len(builds), "interim_failed_builds": sum(not b for b in builds[:-1]), "final_build_passed": builds[-1], "equal_to_full_refresh": h_inc == h_full, "reconcile_ok": rec["ok"], "wrong_segments": rec["orders_with_wrong_point_in_time_segment"]}  # fmt: skip


def history_summary(db: Path) -> dict:
    con = duckdb.connect(str(db))
    r = con.execute(
        """SELECT count(DISTINCT customer_id), count(*), count(*) FILTER (WHERE valid_from > date '1900-01-01') FROM dim_customer_history"""
    ).fetchone()
    cur = con.execute(
        "SELECT count(*) FROM fct_orders f JOIN dim_customer d USING (customer_id) WHERE f.segment <> d.segment"
    ).fetchone()[0]
    con.close()
    return {
        "customers": r[0],
        "history_rows": r[1],
        "segment_changes": r[2],
        "orders_whose_segment_at_order_differs_from_current": cur,
    }


__all__ = ["data"]
