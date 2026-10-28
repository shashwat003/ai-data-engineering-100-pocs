"""A Lake Formation-style permission model: LF-tags with inheritance, tag-based grants, data filters, grants by principal.

Semantics implemented (from the Lake Formation documentation): permissions are additive (no explicit deny); a tag expression
is an AND across tag keys and an OR across the values of one key; tags are inherited database -> table -> column and a child
assignment of the same key overrides the parent's; access is granted per principal to named resources, to tag expressions
or through data cell filters (row predicate + column list). This is a simulation: no Lake Formation service is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

PERMISSIONS = {"DESCRIBE", "SELECT", "ALTER", "DROP", "INSERT", "DELETE"}
SUPER = "IAMAllowedPrincipals"  # legacy group: grants everything to anyone allowed by IAM; the classic LF misconfiguration


class ModelError(ValueError):
    pass


@dataclass
class Column:
    name: str
    type: str


@dataclass
class Table:
    database: str
    name: str
    columns: list[Column]

    @property
    def key(self) -> str:
        return f"{self.database}.{self.name}"


@dataclass(frozen=True)
class DataFilter:
    name: str
    database: str
    table: str
    row_filter: str = "TRUE"  # SQL predicate over the table's columns
    columns: tuple[str, ...] | None = None  # allowed columns; None = all
    excluded_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class Grant:
    principal: str
    permissions: frozenset[str]
    table: str | None = None  # "db.table"
    database: str | None = None  # whole database
    columns: tuple[str, ...] | None = None  # column list on a table grant
    tag_expression: tuple[tuple[str, tuple[str, ...]], ...] | None = None  # ((key, (v1, v2)), ...)
    data_filter: str | None = None
    grant_option: bool = False


@dataclass
class Catalog:
    tables: dict[str, Table] = field(default_factory=dict)
    tag_defs: dict[str, set[str]] = field(default_factory=dict)  # key -> allowed values
    db_tags: dict[str, dict[str, str]] = field(default_factory=dict)
    table_tags: dict[str, dict[str, str]] = field(default_factory=dict)
    column_tags: dict[tuple[str, str], dict[str, str]] = field(
        default_factory=dict
    )  # (table key, column) -> tags
    filters: dict[str, DataFilter] = field(default_factory=dict)
    grants: list[Grant] = field(default_factory=list)

    # --- construction -------------------------------------------------------------------------------
    def add_table(self, t: Table) -> None:
        self.tables[t.key] = t

    def define_tag(self, key: str, values: set[str]) -> None:
        self.tag_defs[key] = set(values)

    def _check_tag(self, key: str, value: str) -> None:
        if key not in self.tag_defs or value not in self.tag_defs[key]:
            raise ModelError(f"tag {key}={value} is not defined")

    def tag_database(self, db: str, **tags: str) -> None:
        for k, v in tags.items():
            self._check_tag(k, v)
        self.db_tags.setdefault(db, {}).update(tags)

    def tag_table(self, table: str, **tags: str) -> None:
        if table not in self.tables:
            raise ModelError(f"unknown table {table}")
        for k, v in tags.items():
            self._check_tag(k, v)
        self.table_tags.setdefault(table, {}).update(tags)

    def tag_column(self, table: str, column: str, **tags: str) -> None:
        if table not in self.tables or column not in {c.name for c in self.tables[table].columns}:
            raise ModelError(f"unknown column {table}.{column}")
        for k, v in tags.items():
            self._check_tag(k, v)
        self.column_tags.setdefault((table, column), {}).update(tags)

    def add_filter(self, f: DataFilter) -> None:
        t = self.tables.get(f"{f.database}.{f.table}")
        if t is None:
            raise ModelError(f"filter {f.name}: unknown table")
        cols = {c.name for c in t.columns}
        bad = (set(f.columns or ()) | set(f.excluded_columns)) - cols
        if bad:
            raise ModelError(f"filter {f.name}: unknown columns {sorted(bad)}")
        try:
            tree = sqlglot.parse_one(f.row_filter)
        except sqlglot.errors.ParseError as e:
            raise ModelError(f"filter {f.name}: unparseable predicate") from e
        if any(
            isinstance(n, (exp.Subquery, exp.Select, exp.Func, exp.Command)) for n in tree.walk()
        ):
            raise ModelError(
                f"filter {f.name}: row filters may only use column comparisons and boolean logic (no subqueries or function calls)"
            )
        refs = {c.name for c in tree.find_all(exp.Column)}
        if refs - cols:
            raise ModelError(f"filter {f.name}: unknown columns {sorted(refs - cols)}")
        self.filters[f.name] = f

    def grant(self, g: Grant) -> None:
        if not g.permissions or not g.permissions <= PERMISSIONS:
            raise ModelError(f"bad permissions {sorted(g.permissions)}")
        targets = sum(x is not None for x in (g.table, g.database, g.tag_expression, g.data_filter))
        if targets != 1:
            raise ModelError(
                "a grant needs exactly one of table, database, tag_expression, data_filter"
            )
        if g.table and g.table not in self.tables:
            raise ModelError(f"unknown table {g.table}")
        if g.data_filter and g.data_filter not in self.filters:
            raise ModelError(f"unknown data filter {g.data_filter}")
        for k, vals in g.tag_expression or ():
            for v in vals:
                self._check_tag(k, v)
        self.grants.append(g)

    # --- tags ----------------------------------------------------------------------------------------
    def effective_column_tags(self, table: str, column: str) -> dict[str, str]:
        """Database tags, overridden by table tags, overridden by column tags."""
        db = table.split(".")[0]
        return {
            **self.db_tags.get(db, {}),
            **self.table_tags.get(table, {}),
            **self.column_tags.get((table, column), {}),
        }

    def effective_table_tags(self, table: str) -> dict[str, str]:
        return {**self.db_tags.get(table.split(".")[0], {}), **self.table_tags.get(table, {})}

    def matches(self, expr: tuple[tuple[str, tuple[str, ...]], ...], tags: dict[str, str]) -> bool:
        return all(tags.get(k) in vals for k, vals in expr)
