"""POC 050: scaffold a data product, build it with real dbt (enforced contract + tests), evaluate SLOs, gate changes."""

from __future__ import annotations

import csv
import os
import random
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
from data_contracts_ci_gate.contract import parse_contract
from data_contracts_ci_gate.evolution import check_evolution

from pocs_common import write_json
from pocs_common.report import markdown_table

from .cli import validate
from .dbtgen import to_dbt_schema
from .product import load_product
from .registry import check_registry
from .scaffold import new_product
from .slo import LOG_DDL, evaluate

POC_DIR = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 3, 10, 12, 0)


def make_seed(path: Path, n: int = 300, *, seed: int = 4, defect: str | None = None) -> None:
    rng = random.Random(seed)
    rows = []
    for i in range(1, n + 1):
        d = date(2026, 3, 8) + timedelta(days=rng.randint(0, 2))
        rows.append(
            {
                "id": i,
                "event_date": d.isoformat(),
                "status": rng.choice(["new", "active", "closed"]),
                "amount": round(rng.uniform(5, 900), 2),
                "contact_email": f"user{i}@example.com",
            }
        )
    if defect == "negative_amount":
        rows[3]["amount"] = -5
    if defect == "bad_status":
        rows[5]["status"] = "archived"
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def dbt_build(project: Path, db: Path) -> tuple[bool, str]:
    env = {
        **os.environ,
        "DBT_DB_PATH": str(db),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        "DO_NOT_TRACK": "1",
    }
    exe = str(Path(sys.executable).with_name("dbt"))
    p = subprocess.run([exe, "build", "--project-dir", str(project), "--profiles-dir", str(project), "--target-path", str(project / "target"), "--log-path", str(project / "logs")],
                       env=env, capture_output=True, text=True, timeout=300, check=False)  # fmt: skip
    return p.returncode == 0, p.stdout[-400:]


def log_db(db: Path, loads: list[tuple[datetime, bool, int]]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(db))
    con.execute(LOG_DDL)
    con.execute("delete from _loads")
    con.executemany("insert into _loads values (?,?,?)", loads)
    return con


def daily(
    n_days: int,
    rows: int,
    *,
    fail_days: tuple[int, ...] = (),
    last_rows: int | None = None,
    end: datetime = NOW - timedelta(hours=6),
):
    out = []
    for k in range(n_days):
        ts = end - timedelta(days=n_days - 1 - k)
        out.append(
            (
                ts,
                k not in fail_days,
                last_rows if (last_rows is not None and k == n_days - 1) else rows + (k % 5) - 2,
            )
        )
    return out


def run() -> dict:
    res: dict = {}
    with tempfile.TemporaryDirectory() as t:
        root = new_product(
            "order_events",
            "sales",
            "sales-data",
            "sales-oncall@example.com",
            t,
            "Order events with a fixed contract.",
        )
        assert not validate(root)
        res["scaffold_files"] = sorted(
            str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
        )
        proj = root / "dbt_project"
        (proj / "seeds").mkdir(exist_ok=True)
        db = Path(t) / "wh.duckdb"
        make_seed(proj / "seeds" / "raw_order_events.csv")
        ok, _ = dbt_build(proj, db)
        res["dbt_clean_build_ok"] = ok
        builds = {}
        for defect in ("negative_amount", "bad_status"):
            make_seed(proj / "seeds" / "raw_order_events.csv", defect=defect)
            bad_db = Path(t) / f"bad_{defect}.duckdb"
            builds[defect], _ = dbt_build(proj, bad_db)
        res["dbt_defect_builds_ok"] = builds
        make_seed(proj / "seeds" / "raw_order_events.csv")
        product = load_product(root / "product.yaml")
        contract = parse_contract((root / "contract.yaml").read_text())
        slo_rows = []
        scen = {
            "healthy": (daily(30, 300), None),
            "stale (no load for 3 days)": (daily(30, 300, end=NOW - timedelta(days=3)), None),
            "volume drop (-70%)": (daily(30, 300, last_rows=90), None),
            "availability burn (4 failed loads)": (daily(30, 300, fail_days=(3, 9, 15, 22)), None),
        }
        for name, (loads, _) in scen.items():
            con = log_db(db, loads)
            r = evaluate(con, product, contract, NOW)
            con.close()
            slo_rows.append(
                [
                    name,
                    r["all_met"],
                    ", ".join(s["name"] for s in r["slis"] if not s["met"]) or "-",
                    r["error_budget"]["remaining"],
                ]
            )
        con = log_db(db, daily(30, 300))
        con.execute("update order_events set amount = null where id <= 20")
        r = evaluate(con, product, contract, NOW)
        slo_rows.append(
            [
                "incomplete (20 null amounts)",
                r["all_met"],
                ", ".join(s["name"] for s in r["slis"] if not s["met"]) or "-",
                r["error_budget"]["remaining"],
            ]
        )
        con.execute("update order_events set status = 'archived' where id <= 20")
        con.execute("update order_events set amount = 10 where amount is null")
        r = evaluate(con, product, contract, NOW)
        slo_rows.append(
            [
                "invalid (20 bad statuses)",
                r["all_met"],
                ", ".join(s["name"] for s in r["slis"] if not s["met"]) or "-",
                r["error_budget"]["remaining"],
            ]
        )
        con.close()
        res["slo_scenarios"] = slo_rows

        # change gate: contract evolution vs version bump, and generated schema.yml drift
        old = contract
        gate = []
        for label, edit in (
            (
                "drop column + patch bump",
                lambda c: c.replace("  - {name: status", "  - {name: status_x").replace(
                    "1.0.0", "1.0.1"
                ),
            ),
            (
                "tighten amount max + minor bump",
                lambda c: c.replace("max: 1000000", "max: 5000").replace("1.0.0", "1.1.0"),
            ),
            (
                "tighten amount max + major bump",
                lambda c: c.replace("max: 1000000", "max: 5000").replace("1.0.0", "2.0.0"),
            ),
            (
                "doc-only change + patch bump",
                lambda c: c.replace("Surrogate key.", "Surrogate row key.").replace(
                    "1.0.0", "1.0.1"
                ),
            ),
        ):
            new = parse_contract(edit((root / "contract.yaml").read_text()))
            ev = check_evolution(old, new)
            gate.append([label, ev.required_bump, ev.actual_bump, "accept" if ev.ok else "REJECT"])
        res["evolution_gate"] = gate
        stale = (proj / "models" / "schema.yml").read_text() != to_dbt_schema(
            parse_contract(
                (root / "contract.yaml").read_text().replace("max: 1000000", "max: 5000")
            ),
            product,
        )
        res["schema_drift_detected"] = stale
        other = load_product(root / "product.yaml")
        res["registry_demo"] = check_registry([other], date(2026, 3, 10))
    return res


def main() -> None:
    res = run()
    write_json(POC_DIR / "results" / "metrics.json", res)
    md = ["# Results (measured by `python -m data_product_template.main`)", "", f"- dbt build of scaffolded product (clean seed): ok={res['dbt_clean_build_ok']}", f"- dbt build with defective seeds (must fail): {res['dbt_defect_builds_ok']}",
          f"- generated schema.yml out of date after a contract edit is detected: {res['schema_drift_detected']}", "", "## SLO scenarios", "",
          markdown_table(["scenario", "all SLOs met", "violated", "error budget left"], res["slo_scenarios"]), "", "## Contract change gate", "",
          markdown_table(["change", "required bump", "actual bump", "verdict"], res["evolution_gate"])]  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
