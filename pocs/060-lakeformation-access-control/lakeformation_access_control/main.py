"""POC 060: Lake Formation-style fine-grained access (tags, data filters, column grants) enforced on DuckDB, with attack attempts and lint."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .demo_data import build
from .enforce import AccessDenied, SecureEngine
from .lint import lint
from .policy import visible_matrix

POC_DIR = Path(__file__).resolve().parent.parent
ROLES = ["analyst_eu", "data_scientist", "compliance", "support", "stranger"]
# name -> (role, sql, expectation) where expectation is "deny" or the maximum number of rows a safe engine may return
ATTACKS = {
    "select forbidden column": ("analyst_eu", "SELECT email FROM sales.customers", "deny"),
    "forbidden column in WHERE (inference oracle)": (
        "support",
        "SELECT name FROM sales.customers WHERE email LIKE '%1@%'",
        "deny",
    ),
    "forbidden column via subquery": (
        "support",
        "SELECT name FROM (SELECT * FROM sales.customers) t WHERE t.email IS NOT NULL",
        "deny",
    ),
    "forbidden column via CTE": (
        "analyst_eu",
        "WITH c AS (SELECT email FROM sales.customers) SELECT * FROM c",
        "deny",
    ),
    "UNION tries to widen rows (non-EU orders)": (
        "analyst_eu",
        "SELECT customer_id FROM sales.customers UNION ALL SELECT customer_id FROM sales.orders WHERE region <> 'EU'",
        30,
    ),
    "SELECT * returns only permitted columns": ("support", "SELECT * FROM sales.customers", 90),
    "table with no grant": ("support", "SELECT * FROM sales.orders", "deny"),
    "unknown role": ("stranger", "SELECT * FROM sales.customers", "deny"),
    "multi-statement": ("compliance", "SELECT 1; DROP TABLE customers", "deny"),
    "write attempt": ("compliance", "INSERT INTO customers VALUES (1,'a','b','c','d')", "deny"),
    "table function reads a file": ("compliance", "SELECT * FROM read_csv('/etc/passwd')", "deny"),
}


def run() -> dict:
    cat, con = build()
    eng = SecureEngine(cat, con)
    res = {
        "visible_columns": visible_matrix(cat, ROLES),
        "row_counts": {},
        "attacks": {},
        "lint": lint(cat, pii_roles={"compliance"}),
    }
    for role in ROLES[:4]:
        res["row_counts"][role] = {
            t: len(eng.query(role, f"SELECT 1 FROM {t}"))
            for t in ("sales.customers", "sales.orders")
            if res["visible_columns"][role][t]
        }
    for name, (role, sql, expect) in ATTACKS.items():
        try:
            rows = eng.query(role, sql)
            ok = expect != "deny" and len(rows) <= expect
            res["attacks"][name] = (
                f"{'OK' if ok else 'LEAK'}: returned {len(rows)} rows (limit {expect})"
            )
        except AccessDenied as e:
            res["attacks"][name] = (
                f"{'OK' if expect == 'deny' else 'WRONGLY DENIED'}: denied ({e})"[:110]
            )
        except (
            Exception
        ) as e:  # an engine error also means nothing leaked, but it is reported as a defect to fix
            res["attacks"][name] = f"ENGINE ERROR: {type(e).__name__}"
    return res


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    md = [
        "# Results (measured by `python -m lakeformation_access_control.main`; Lake Formation semantics SIMULATED on DuckDB, no AWS service)",
        "",
        "## Visible columns per role",
        "",
    ]
    rows = [
        [p, t, ", ".join(c) or "-"] for p, d in r["visible_columns"].items() for t, c in d.items()
    ]
    md += [
        markdown_table(["role", "table", "columns"], rows),
        "",
        "## Rows visible per role (customers / orders have 90 / 300 rows)",
        "",
    ]
    md += [f"- {p}: {d}" for p, d in r["row_counts"].items()]
    md += [
        "",
        "## Attack attempts",
        "",
        markdown_table(["attempt", "outcome"], [[k, v] for k, v in r["attacks"].items()]),
        "",
        "## Lint findings",
        "",
    ] + [f"- {x}" for x in r["lint"]]
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
