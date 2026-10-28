"""Validate model SQL against the Redshift dialect (syntax via sqlglot, plus constructs Redshift lacks) and Redshift dbt configs.

This checks SYNTAX and a short list of unsupported constructs. It does not execute anything on Redshift, so semantic differences
(type coercion, NULL ordering, function edge cases) remain unverified.
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlglot
from sqlglot import exp

UNSUPPORTED = {
    exp.TryCast: "TRY_CAST does not exist in Redshift",
    exp.Unnest: "UNNEST/arrays are not supported (use SUPER)",
    exp.Struct: "STRUCT is not supported",
    exp.Lambda: "lambda expressions are not supported",
}
DIST_STYLES = {"all", "even", "auto"}
STRATEGIES = {"append", "delete+insert", "merge"}


def lint_sql(sql: str) -> list[str]:
    try:
        trees = sqlglot.parse(sql, read="redshift")
    except sqlglot.errors.ParseError as e:
        return [f"does not parse as Redshift SQL: {str(e)[:120]}"]
    out = []
    for tree in trees:
        if tree is None:
            continue
        for node in tree.walk():
            for typ, why in UNSUPPORTED.items():
                if isinstance(node, typ):
                    out.append(why)
    return sorted(set(out))


def lint_compiled(target_dir: str | Path) -> dict[str, list[str]]:
    res = {}
    for f in sorted((Path(target_dir) / "compiled").rglob("*.sql")):
        if "/tests/" in str(f) or "schema.yml" in str(f):
            continue
        res[f.stem] = lint_sql(f.read_text())
    return res


def check_configs(manifest_path: str | Path, columns: dict[str, list[str]]) -> list[str]:
    """Check dist/sort/incremental configs of every model against the model's real columns."""
    m = json.loads(Path(manifest_path).read_text())
    out = []
    for node in m["nodes"].values():
        if node["resource_type"] != "model":
            continue
        name, cfg = node["name"], node["config"]
        cols = set(columns.get(name, []))
        dist = cfg.get("dist")
        if dist is not None and str(dist).lower() not in DIST_STYLES and dist not in cols:
            out.append(f"{name}: dist key {dist!r} is not a column")
        sort = cfg.get("sort")
        for s in [sort] if isinstance(sort, str) else (sort or []):
            if s not in cols:
                out.append(f"{name}: sort key {s!r} is not a column")
        if cfg.get("materialized") == "incremental":
            strat = cfg.get("incremental_strategy")
            if strat not in STRATEGIES:
                out.append(f"{name}: incremental strategy {strat!r} unsupported on Redshift")
            if strat in ("delete+insert", "merge") and not cfg.get("unique_key"):
                out.append(f"{name}: {strat} needs a unique_key")
    return out
