"""Query enforcement: rewrite a SELECT so each table is replaced by a secured subquery built from the principal's effective access."""

from __future__ import annotations

import duckdb
import sqlglot
from sqlglot import exp

from .model import Catalog
from .policy import Access, can_describe, effective_access


class AccessDenied(PermissionError):
    pass


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def secured_subquery(cat: Catalog, acc: Access, view_of: dict[str, str]) -> str:
    """Union semantics: a row is visible if ANY slice's predicate holds; a column is non-NULL only where a matching slice exposes it."""
    t = cat.tables[acc.table]
    cols = [c.name for c in t.columns if c.name in acc.columns]
    any_row = " OR ".join(f"({s.row_filter})" for s in acc.slices)
    sel = []
    for c in cols:
        conds = [f"({s.row_filter})" for s in acc.slices if c in s.columns]
        sel.append(f"CASE WHEN {' OR '.join(conds)} THEN {_q(c)} END AS {_q(c)}")
    return f"(SELECT {', '.join(sel)} FROM {view_of[acc.table]} WHERE {any_row})"


def authorise_and_rewrite(cat: Catalog, principal: str, sql: str, view_of: dict[str, str]) -> str:
    try:
        trees = sqlglot.parse(sql, dialect="duckdb")
    except sqlglot.errors.ParseError as e:
        raise AccessDenied("query could not be parsed") from e
    if len(trees) != 1 or not isinstance(trees[0], (exp.Select, exp.Union, exp.Subquery)):
        raise AccessDenied("exactly one SELECT statement is allowed")
    tree = trees[0]
    if any(
        isinstance(
            n,
            (
                exp.Command,
                exp.Insert,
                exp.Update,
                exp.Delete,
                exp.Create,
                exp.Drop,
                exp.Copy,
                exp.Pragma,
            ),
        )
        for n in tree.walk()
    ):
        raise AccessDenied("only SELECT is allowed")
    ctes = {c.alias for c in tree.find_all(exp.CTE)} | {
        n.alias for n in tree.find_all(exp.Subquery) if n.alias
    }
    refs: dict[str, str] = {}  # alias-or-name -> qualified table key
    for tbl in tree.find_all(exp.Table):
        if not tbl.name:
            raise AccessDenied("table functions are not allowed")
        if tbl.name in ctes and not tbl.db:
            continue
        key = f"{tbl.db}.{tbl.name}" if tbl.db else None
        if key is None:
            key = next((k for k in cat.tables if k.split(".")[1] == tbl.name), None)
        if key not in cat.tables or not can_describe(cat, principal, key):
            raise AccessDenied(
                f"table {tbl.sql()} does not exist or access is denied"
            )  # same message for both: no existence oracle
        if refs.get(tbl.alias_or_name, key) != key:
            raise AccessDenied(
                f"alias {tbl.alias_or_name!r} is bound to different tables in one query"
            )
        refs[tbl.alias_or_name] = key
    accesses = {k: effective_access(cat, principal, k) for k in set(refs.values())}
    for k, acc in accesses.items():
        if not acc.slices:
            raise AccessDenied(f"no SELECT access to {k}")
    # column checks
    for col in tree.find_all(exp.Column):
        if col.table and col.table not in refs and col.table not in ctes:
            raise AccessDenied(f"unknown table reference {col.table!r}")
        # an alias of a CTE/subquery says nothing about the base table, so check the name against every referenced table
        owners = [refs[col.table]] if col.table in refs else list(set(refs.values()))
        if not owners:
            continue
        known = [k for k in owners if col.name in {c.name for c in cat.tables[k].columns}]
        if known and not any(col.name in accesses[k].columns for k in known):
            raise AccessDenied(f"column {col.name} is not accessible")
    # SELECT * needs no expansion: each table is replaced by a subquery that only exposes permitted columns.
    for tbl in list(tree.find_all(exp.Table)):
        if tbl.alias_or_name in refs and not (tbl.name in ctes and not tbl.db):
            key = refs[tbl.alias_or_name]
            sub = sqlglot.parse_one(secured_subquery(cat, accesses[key], view_of), dialect="duckdb")
            alias = tbl.alias or tbl.name
            tbl.replace(
                exp.Subquery(this=sub.this, alias=exp.TableAlias(this=exp.to_identifier(alias)))
            )
    return tree.sql(dialect="duckdb")


class SecureEngine:
    def __init__(self, cat: Catalog, con: duckdb.DuckDBPyConnection):
        self.cat, self.con = cat, con
        self.view_of = {k: _q(t.name) for k, t in cat.tables.items()}

    def query(self, principal: str, sql: str, max_rows: int = 10_000) -> list[tuple]:
        rewritten = authorise_and_rewrite(self.cat, principal, sql, self.view_of)
        return self.con.execute(rewritten).fetchmany(max_rows)
