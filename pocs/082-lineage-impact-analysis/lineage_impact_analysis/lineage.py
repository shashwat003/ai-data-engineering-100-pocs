"""Column-level lineage from SQL (sqlglot) and impact analysis over the resulting graph (networkx)."""

from __future__ import annotations

import networkx as nx
import sqlglot
from sqlglot import exp
from sqlglot.lineage import lineage


class LineageError(ValueError):
    """The SQL references a table that is neither raw nor an earlier model: refuse to guess."""


def check_tables(sql: str, schema: dict) -> None:
    tree = sqlglot.parse_one(sql, dialect="duckdb")
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        if t.name in ctes and not t.db:
            continue
        if t.name not in schema.get(t.db, {}):
            raise LineageError(f"unknown table {t.db}.{t.name}")


def schema_for(models: dict[str, str], raw: dict) -> dict:
    """Schema dict {db: {table: {col: type}}} including each model's output columns (types unknown -> text)."""
    schema = {db: {t: dict(cols) for t, cols in tabs.items()} for db, tabs in raw.items()}
    for name in models:  # resolve in dependency order so SELECT * can be expanded
        db, table = name.split(".")
        cols = output_columns(models[name], schema)
        schema.setdefault(db, {})[table] = dict.fromkeys(cols, "text")
    return schema


def output_columns(sql: str, schema: dict) -> list[str]:
    tree = sqlglot.parse_one(sql, dialect="duckdb")
    from sqlglot.optimizer.qualify import qualify

    q = qualify(tree, schema=schema, dialect="duckdb", validate_qualify_columns=False)
    return [s.alias_or_name for s in q.selects]


def topo(models: dict[str, str]) -> list[str]:
    g = nx.DiGraph()
    for name, sql in models.items():
        g.add_node(name)
        for t in sqlglot.parse_one(sql, dialect="duckdb").find_all(exp.Table):
            dep = f"{t.db}.{t.name}"
            if dep in models:
                g.add_edge(dep, name)
    return list(nx.topological_sort(g))


def column_edges(models: dict[str, str], raw: dict) -> dict[str, set[str]]:
    """One-hop lineage: 'model.col' -> {'upstream_table.col', ...}."""
    order = topo(models)
    ordered = {m: models[m] for m in order}
    schema: dict = {db: {t: dict(c) for t, c in tabs.items()} for db, tabs in raw.items()}
    edges: dict[str, set[str]] = {}
    for name in ordered:
        sql = ordered[name]
        check_tables(sql, schema)
        cols = output_columns(sql, schema)
        db, table = name.split(".")
        for col in cols:
            node = lineage(col, sql, schema=schema, dialect="duckdb")
            srcs: set[str] = set()
            stack = [node]
            while stack:
                n = stack.pop()
                if not n.downstream:
                    if isinstance(n.source, exp.Table):
                        srcs.add(f"{n.source.db}.{n.source.name}.{n.name.split('.')[-1]}")
                stack.extend(n.downstream)
            edges[f"{name}.{col}"] = srcs
        schema.setdefault(db, {})[table] = dict.fromkeys(cols, "text")
    return edges


def build_graph(edges: dict[str, set[str]]) -> nx.DiGraph:
    g = nx.DiGraph()
    for dst, srcs in edges.items():
        for s in srcs:
            g.add_edge(s, dst)  # direction: data flows upstream -> downstream
    return g


def impact(g: nx.DiGraph, changed: str) -> dict:
    """Everything downstream of a changed column (or of all columns of a table if `changed` is a table)."""
    starts = [changed] if changed in g else [n for n in g if n.rsplit(".", 1)[0] == changed]
    down = set().union(*(nx.descendants(g, s) for s in starts)) if starts else set()
    tables = sorted({n.rsplit(".", 1)[0] for n in down})
    depth = max(
        (max(nx.single_source_shortest_path_length(g, s).values()) for s in starts), default=0
    )
    return {"columns": sorted(down), "tables": tables, "depth": depth}


def breaking_change(old: dict[str, str], new: dict[str, str], raw: dict) -> list[str]:
    """Models whose SQL no longer resolves, or whose output columns lost names that downstream models read."""
    old_e, problems = column_edges(old, raw), []
    try:
        new_e = column_edges(new, raw)
    except Exception as e:  # unresolved column/table in a downstream model
        return [f"project no longer compiles: {type(e).__name__}: {str(e)[:100]}"]
    removed = set(old_e) - set(new_e)
    for dst, srcs in old_e.items():
        for s in srcs:
            if s in removed:
                problems.append(f"{dst} reads {s}, which no longer exists")
    return sorted(problems)
