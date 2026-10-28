"""Effective access: which columns and rows a principal may see in a table, and why."""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import SUPER, Catalog, Grant


@dataclass
class Slice:
    """One grant's contribution: its columns and the row predicate under which they are visible."""

    source: str
    columns: frozenset[str]
    row_filter: str = "TRUE"


@dataclass
class Access:
    table: str
    slices: list[Slice] = field(default_factory=list)

    @property
    def columns(self) -> frozenset[str]:
        return frozenset().union(*[s.columns for s in self.slices]) if self.slices else frozenset()

    @property
    def can_describe_only(self) -> bool:
        return not self.slices


def _describe(g: Grant) -> str:
    if g.data_filter:
        return f"data filter {g.data_filter}"
    if g.tag_expression:
        return "tag expression " + " AND ".join(f"{k} IN {list(v)}" for k, v in g.tag_expression)
    return f"{'database ' + g.database if g.database else 'table ' + g.table}"


def effective_access(
    cat: Catalog, principal: str, table: str, *, honour_super: bool = True
) -> Access:
    t = cat.tables[table]
    all_cols = [c.name for c in t.columns]
    acc = Access(table)
    principals = {principal} | ({SUPER} if honour_super else set())
    for g in cat.grants:
        if g.principal not in principals or "SELECT" not in g.permissions:
            continue
        if g.data_filter:
            f = cat.filters[g.data_filter]
            if f"{f.database}.{f.table}" != table:
                continue
            cols = [
                c
                for c in (f.columns if f.columns is not None else all_cols)
                if c not in f.excluded_columns
            ]
            acc.slices.append(Slice(_describe(g), frozenset(cols), f.row_filter))
        elif g.table == table:
            acc.slices.append(
                Slice(_describe(g), frozenset(g.columns if g.columns is not None else all_cols))
            )
        elif g.database == table.split(".")[0]:
            acc.slices.append(Slice(_describe(g), frozenset(all_cols)))
        elif g.tag_expression:
            # a tag grant exposes the columns whose effective tags satisfy the expression (table-level tags apply to all columns)
            cols = [
                c
                for c in all_cols
                if cat.matches(g.tag_expression, cat.effective_column_tags(table, c))
            ]
            if cols:
                acc.slices.append(Slice(_describe(g), frozenset(cols)))
    return acc


def can_describe(cat: Catalog, principal: str, table: str) -> bool:
    """DESCRIBE is implied by any grant that touches the table (including column-level access)."""
    if effective_access(cat, principal, table).slices:
        return True
    for g in cat.grants:
        if (
            g.principal in {principal, SUPER}
            and "DESCRIBE" in g.permissions
            and (g.table == table or g.database == table.split(".")[0])
        ):
            return True
    return False


def visible_matrix(cat: Catalog, principals: list[str]) -> dict[str, dict[str, list[str]]]:
    return {
        p: {t: sorted(effective_access(cat, p, t).columns) for t in cat.tables} for p in principals
    }
