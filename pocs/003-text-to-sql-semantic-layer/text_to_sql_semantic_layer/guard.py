"""SQL validation (layer 1 of 2). The executor (executor.py) independently enforces the same
boundaries inside SQLite, so a validator bug alone cannot make a query unsafe.

Checks: exactly one statement; SELECT only; known tables only (no schema-qualified, no sqlite_*,
no table-valued functions); every column resolvable and not restricted (stars are expanded so
`SELECT *` cannot leak a restricted column); function allowlist; no window functions or recursive
CTEs; joins must have conditions; bounded depth and size; LIMIT required and capped.
The SQL that is executed is the parser's regeneration of what was validated, not the raw input.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from .schema import SchemaInfo

logging.getLogger("sqlglot").setLevel(logging.ERROR)  # 'unsupported syntax' notices are expected

# sqlglot's canonical function names (strftime parses to time_to_str, ifnull to coalesce, ...).
ALLOWED_FUNCTIONS = frozenset(
    "count sum avg min max round abs coalesce nullif lower upper length substring trim "
    "time_to_str ts_or_ds_to_timestamp date cast case if".split()
)


@dataclass(frozen=True)
class Policy:
    blocked_columns: frozenset[tuple[str, str]] = frozenset({("customers", "email")})
    allowed_functions: frozenset[str] = ALLOWED_FUNCTIONS
    default_limit: int = 100
    max_limit: int = 1000
    max_joins: int = 5
    max_subquery_depth: int = 3
    max_sql_chars: int = 4000


@dataclass
class Validation:
    ok: bool
    sql: str | None = None  # the SQL to execute (regenerated, with LIMIT enforced)
    reasons: list[str] = field(default_factory=list)
    rewrites: list[str] = field(default_factory=list)


def _reject(*reasons: str) -> Validation:
    return Validation(False, None, list(reasons))


def _func_name(node: exp.Func) -> str:
    return (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()


def _depth(tree: exp.Expression) -> int:
    """Deepest nesting of SELECTs (a CTE or subquery body counts as one level inside its parent)."""
    deepest = 0
    for select in tree.find_all(exp.Select):
        level, parent = 0, select.parent
        while parent is not None:
            level += isinstance(parent, exp.Select)
            parent = parent.parent
        deepest = max(deepest, level)
    return deepest


def validate(sql: str, schema: SchemaInfo, policy: Policy | None = None) -> Validation:
    policy = policy or Policy()
    if not sql or not sql.strip():
        return _reject("empty query")
    if len(sql) > policy.max_sql_chars:
        return _reject(f"query longer than {policy.max_sql_chars} characters")
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except SqlglotError as exc:
        return _reject(f"cannot parse: {str(exc)[:120]}")
    if len(statements) != 1 or statements[0] is None:
        return _reject("exactly one statement is allowed")
    tree = statements[0]
    if not isinstance(tree, exp.Select):
        return _reject(f"only SELECT is allowed (got {type(tree).__name__})")

    known = {t.lower() for t in schema.tables}
    ctes = {c.alias.lower() for c in tree.find_all(exp.CTE)}
    reasons: list[str] = []
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        if t.args.get("db") or t.args.get("catalog"):
            reasons.append(f"schema-qualified table {t.sql()!r} is not allowed")
        elif not isinstance(t.this, exp.Identifier):
            reasons.append("table-valued functions are not allowed")
        elif name.startswith("sqlite_"):
            reasons.append(f"internal table {name!r} is not allowed")
        elif name not in known and name not in ctes:
            reasons.append(f"unknown table {name!r}")
    for f in tree.find_all(exp.Func):
        if isinstance(f, exp.Connector):  # sqlglot models AND/OR as functions; they are operators
            continue
        name = _func_name(f)
        if name not in policy.allowed_functions:
            reasons.append(f"function {name!r} is not allowed")
    if any(tree.find_all(exp.Window)):
        reasons.append("window functions are not allowed")
    if any(w.args.get("recursive") for w in tree.find_all(exp.With)):
        reasons.append("recursive CTEs are not allowed")
    joins = list(tree.find_all(exp.Join))
    if len(joins) > policy.max_joins:
        reasons.append(f"more than {policy.max_joins} joins")
    if any(not (j.args.get("on") or j.args.get("using")) for j in joins):
        reasons.append("every join needs an ON/USING condition (no cartesian products)")
    if _depth(tree) > policy.max_subquery_depth:
        reasons.append("subqueries nested too deeply")
    if reasons:
        return _reject(*dict.fromkeys(reasons))

    try:  # resolves every column and expands SELECT * so restricted columns become visible
        qualified = qualify(
            tree.copy(),
            schema=schema.sqlglot_schema(),
            dialect="sqlite",
            validate_qualify_columns=True,
        )
    except SqlglotError as exc:
        return _reject(f"unresolvable or ambiguous column: {str(exc)[:120]}")
    for scope in traverse_scope(qualified):
        for col in scope.columns:
            src = scope.sources.get(col.table)
            if (
                isinstance(src, exp.Table)
                and (src.name.lower(), col.name.lower()) in policy.blocked_columns
            ):
                reasons.append(f"column {src.name}.{col.name} is restricted")
    if reasons:
        return _reject(*dict.fromkeys(reasons))

    rewrites: list[str] = []
    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(policy.default_limit)
        rewrites.append(f"added LIMIT {policy.default_limit}")
    else:
        value = limit.expression
        if not (isinstance(value, exp.Literal) and value.is_int):
            return _reject("LIMIT must be an integer literal")
        if int(value.name) > policy.max_limit:
            tree = tree.limit(policy.max_limit)
            rewrites.append(f"capped LIMIT {value.name} to {policy.max_limit}")
    return Validation(True, tree.sql(dialect="sqlite", comments=False), [], rewrites)
