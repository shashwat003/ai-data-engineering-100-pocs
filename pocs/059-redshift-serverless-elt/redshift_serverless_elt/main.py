"""POC 059: Redshift Serverless ELT. REAL: dbt-core + DuckDB execution, COPY semantics (local), Redshift-dialect SQL validation
(syntax only), Terraform plan (offline). NOT RUN: Redshift, dbt-redshift, COPY from S3, Terraform apply."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from . import data, elt, pipeline, redshift_sql
from .costs import check_plan, monthly_cost
from .loader import CopyError, Warehouse, copy_sql

POC_DIR = Path(__file__).resolve().parent.parent
VARIANTS = {
    "default": [],
    "publicly accessible": ["-var=publicly_accessible=true"],
    "oversized base capacity": ["-var=base_capacity_rpu=128", "-var=max_capacity_rpu=512"],
}


def run(quick: bool = False) -> dict:
    out: dict = {}
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        manifest = data.generate(root / "landing")
        out["expected_defects"] = manifest["expected"]
        db = root / "w.duckdb"
        w = Warehouse(db)
        # COPY is atomic past MAXERROR: batch 2 has 3 unparsable amounts
        try:
            w.copy("customers", root / "landing/b001/customers.csv", "b001")
            w.copy("orders", root / "landing/b001/orders.csv", "b001")
            w.copy("orders", root / "landing/b002/orders.csv", "b002", maxerror=1)
            strict = "loaded"
        except CopyError as e:
            strict = f"CopyError: {e}"
        out["maxerror_1_on_batch_2"] = {
            "result": strict,
            "orders_rows_after": w.con.execute("SELECT COUNT(*) FROM raw.orders").fetchone()[0],
        }
        loads = w.load_landing(root / "landing")
        out["loads"] = [x for x in loads if x["status"] == "loaded"]
        out["replay"] = {
            "loaded": sum(x["status"] == "loaded" for x in w.load_landing(root / "landing")),
            "skipped": sum(x["status"] == "skipped" for x in w.load_landing(root / "landing")),
        }
        out["load_errors"] = w.con.execute(
            "SELECT entity, batch_id, column_name, raw_value FROM raw.load_errors ORDER BY batch_id, line"
        ).fetchall()
        w.close()
        out["dbt_build"] = elt.build(root / "full", db)
        out["reconcile"] = elt.reconcile(db)
        out["redshift_sql"] = redshift_sql.lint_compiled(root / "full" / "target")
        out["redshift_configs"] = redshift_sql.check_configs(
            root / "full" / "target" / "manifest.json", pipeline.columns(db)
        )
        out["history"] = elt.history_summary(db)
        out["arrivals"] = [
            elt.arrival_scenario(root / "arrivals", root / "landing", o, seed=3)
            for o in (elt.ARRIVALS[:2] if quick else elt.ARRIVALS)
        ]
    out["copy_example"] = copy_sql(
        "orders",
        "s3://elt-landing-123456789012/b001/orders.csv",
        "arn:aws:iam::123456789012:role/redshift-copy-role",
        maxerror=5,
    )
    out["unsupported_examples"] = {
        sql: redshift_sql.lint_sql(sql)
        for sql in ("select try_cast(a as int) from t", "select unnest(x) from t", "select a from")
    }
    plans = {}
    for name, extra in VARIANTS.items():
        plan = pipeline.terraform_plan(extra)
        plans[name] = None if plan is None else check_plan(plan)
    out["terraform"] = plans
    out["cost_model"] = {f"{r} RPU, 4 runs/day x 5 min": monthly_cost(r, 4, 300) for r in (8, 32)}
    return out


def render(res: dict) -> str:
    rec, hist = res["reconcile"], res["history"]
    loaded = sum(x["rows_loaded"] for x in res["loads"])
    rej = sum(x["rows_rejected"] for x in res["loads"])
    md = [
        "# Results (POC 059: REAL dbt-core + DuckDB, local COPY semantics, Redshift-dialect syntax checks, offline Terraform plan; NOTHING run on Redshift or AWS)",
        f"Expected injected defects: {res['expected_defects']}. COPY loaded {loaded} rows and rejected {rej} unparsable ones (MAXERROR 5), logging each reject in `raw.load_errors`.",
        "\n## COPY semantics\n",
        markdown_table(
            ["check", "result"],
            [
                [
                    "MAXERROR 1 on a batch with 3 bad rows",
                    f"{res['maxerror_1_on_batch_2']['result'][:90]}; orders rows after: {res['maxerror_1_on_batch_2']['orders_rows_after']}",
                ],
                [
                    "replaying every file",
                    f"{res['replay']['loaded']} loaded, {res['replay']['skipped']} skipped (ledger)",
                ],
                [
                    "rejected cells",
                    "; ".join(f"{e}/{b} {c}={v!r}" for e, b, c, v in res["load_errors"]),
                ],
            ],
        ),  # fmt: skip
        "\n## dbt build and reconciliation\n",
        f"dbt build: {res['dbt_build']}. Independent recomputation from raw (DuckDB ASOF join, different SQL): valid orders {rec['valid_orders_expected']} vs fct_orders {rec['fct_orders']}; rejected {rec['rejected_expected']} vs {rec['rejected_orders']}; "
        f"orders with a wrong point-in-time segment: **{rec['orders_with_wrong_point_in_time_segment']}**; revenue by segment matches: {rec['segment_revenue_matches']}; revenue by current segment matches: {rec['current_segment_revenue_matches']} -> **{'consistent' if rec['ok'] else 'MISMATCH'}**.",
        "\n## Business rule: segment AS OF THE ORDER DATE (point-in-time)\n",
        f"`dim_customer_history` keeps every segment/country change (SCD2): {hist['customers']} customers, {hist['history_rows']} history rows, {hist['segment_changes']} changes. `fct_orders.segment` is the segment valid on the order date; {hist['orders_whose_segment_at_order_differs_from_current']} orders belong to customers whose segment has since changed, so `revenue_by_segment` (as of order date) and `revenue_by_current_segment` (today's segmentation) deliberately differ. The earlier version of this POC carried the segment as of LOAD time, which neither rule asks for (42 of 992 rows disagreed with a full refresh).",
        "\n### Incremental build vs one full refresh, by arrival order (all columns incl. segment and country)\n",
        markdown_table(
            [
                "arrival order",
                "dbt builds",
                "interim builds stopped by data tests",
                "final build passed",
                "equals full refresh",
                "wrong point-in-time segments",
            ],
            [
                [
                    a["arrival"],
                    a["builds"],
                    a["interim_failed_builds"],
                    a["final_build_passed"],
                    a["equal_to_full_refresh"],
                    a["wrong_segments"],
                ]
                for a in res["arrivals"]
            ],
        ),  # fmt: skip
        "A stopped interim build is the data-quality gate working: orders that arrive before their customers fail the not-null/relationships tests, and the next build repairs them.",
        "\n## Redshift dialect checks (syntax only)\n",
        markdown_table(
            ["compiled model", "findings"],
            [[k, "; ".join(v) or "none"] for k, v in res["redshift_sql"].items()],
        ),
        f"\nRedshift dbt config findings (dist/sort/strategy): {res['redshift_configs'] or 'none'}. Negative examples: "
        + "; ".join(f"`{k}` -> {v[0][:60]}" for k, v in res["unsupported_examples"].items() if v)
        + ".",
        f"\nCOPY statement the production loader would run: `{res['copy_example']}`",
        "\n## Terraform plan checks (offline, mock credentials, never applied)\n",
        markdown_table(
            ["variant", "findings"],
            [
                [k, "terraform not installed" if v is None else ("; ".join(v) or "none")]
                for k, v in res["terraform"].items()
            ],
        ),
        "\n## Modelled monthly compute (ASSUMED $0.375/RPU-hour, 60 s minimum)\n",
        markdown_table(
            ["workload", "RPU-hours", "compute USD"],
            [[k, v["rpu_hours"], v["compute_usd"]] for k, v in res["cost_model"].items()],
        ),
        "",
    ]
    return "\n".join(md)


def main() -> None:
    res = run(bool(os.environ.get("DEMO_QUICK")))
    out = POC_DIR / "results"
    out.mkdir(exist_ok=True)
    write_json(out / "metrics.json", res)
    (out / "results.md").write_text(render(res))
    print(render(res))


if __name__ == "__main__":
    main()
