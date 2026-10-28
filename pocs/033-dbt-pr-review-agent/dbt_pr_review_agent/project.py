"""Light-weight reader of a dbt project directory (no dbt needed): models, refs, tests, columns via sqlglot."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
import yaml
from sqlglot import exp

REF = re.compile(r"\{\{\s*ref\(\s*['\"](\w+)['\"]\s*\)\s*\}\}")
CONFIG = re.compile(r"\{\{\s*config\((.*?)\)\s*\}\}", re.S)
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)


@dataclass
class Model:
    name: str
    path: str
    raw: str
    refs: list[str]
    sql: str  # jinja removed
    ast: exp.Expression | None
    materialized: str = "view"
    description: str = ""
    tests: list[str] = field(default_factory=list)  # "column:test" or "model:test"
    columns: list[str] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    joins: list[tuple[str, str]] = field(default_factory=list)
    where: str = ""
    hardcoded: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)


def render(raw: str) -> tuple[str, list[str], str]:
    refs = REF.findall(raw)
    mat = "view"
    m = CONFIG.search(raw)
    if m:
        mm = re.search(r"materialized\s*=\s*['\"](\w+)['\"]", m.group(1))
        mat = mm.group(1) if mm else mat
    sql = REF.sub(lambda x: x.group(1), CONFIG.sub("", raw))
    return JINJA.sub("", sql), refs, mat


def _select(ast: exp.Expression) -> exp.Select | None:
    return ast if isinstance(ast, exp.Select) else ast.find(exp.Select)


def analyse(m: Model, upstream_cols: dict[str, list[str]]) -> None:
    try:
        m.ast = sqlglot.parse_one(m.sql, read="duckdb")
    except sqlglot.errors.ParseError:
        m.ast = None
        return
    sel = _select(m.ast)
    cols: list[str] = []
    for e in sel.expressions:
        if isinstance(e, exp.Star):
            for r in m.refs:
                cols += upstream_cols.get(r, [])
        else:
            cols.append(e.alias_or_name)
    m.columns = cols
    g = sel.args.get("group")
    m.group_by = sorted(x.sql() for x in g.expressions) if g else []
    m.joins = [
        ((j.args.get("side") or "").lower() + " " + (j.args.get("kind") or "").lower()).strip()
        or "inner"
        for j in sel.args.get("joins", [])
    ]
    m.joins = [
        (k if k else "inner", j.this.name)
        for k, j in zip(m.joins, sel.args.get("joins", []), strict=True)
    ]
    w = sel.args.get("where")
    m.where = w.this.sql(normalize=True) if w else ""
    m.comments = [c.strip() for n in m.ast.walk() for c in (n.comments or [])] if m.ast else []
    cte_names = {c.alias for c in m.ast.find_all(exp.CTE)}
    tables = {t.name for t in m.ast.find_all(exp.Table)}
    m.hardcoded = sorted(t for t in tables if t not in m.refs and t not in cte_names)


def load(project_dir: str | Path) -> dict[str, Model]:
    root = Path(project_dir)
    models: dict[str, Model] = {}
    seed_cols = {}
    for p in sorted((root / "seeds").glob("*.csv")):
        with p.open() as fh:
            seed_cols[p.stem] = next(csv.reader(fh))
    for p in sorted((root / "models").glob("*.sql")):
        raw = p.read_text()
        sql, refs, mat = render(raw)
        models[p.stem] = Model(p.stem, str(p.relative_to(root)), raw, refs, sql, None, mat)
    for p in sorted((root / "models").glob("*.yml")):
        doc = yaml.safe_load(p.read_text()) or {}
        for md in doc.get("models", []) or []:
            m = models.get(md["name"])
            if not m:
                continue
            m.description = (md.get("description") or "").strip()
            for t in md.get("tests", []) or []:
                m.tests.append("model:" + (t if isinstance(t, str) else next(iter(t))))
            for c in md.get("columns", []) or []:
                for t in c.get("tests", []) or []:
                    m.tests.append(f"{c['name']}:" + (t if isinstance(t, str) else next(iter(t))))
    done, order = set(seed_cols), []
    pending = dict(models)
    while pending:
        progressed = False
        for n, m in list(pending.items()):
            if all(r in done for r in m.refs):
                order.append(n)
                done.add(n)
                del pending[n]
                progressed = True
        if not progressed:
            raise ValueError(f"cyclic or unresolved refs among {sorted(pending)}")
    up = dict(seed_cols)
    for n in order:
        analyse(models[n], up)
        up[n] = models[n].columns
    return models


def children(models: dict[str, Model]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {n: [] for n in models}
    for n, m in models.items():
        for r in m.refs:
            out.setdefault(r, []).append(n)
    return out
