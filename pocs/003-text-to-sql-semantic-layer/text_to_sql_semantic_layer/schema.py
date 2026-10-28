"""Schema introspection from a SQLite file (opened read-only)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    primary_key: bool


@dataclass(frozen=True)
class SchemaInfo:
    tables: dict[str, tuple[Column, ...]]
    foreign_keys: tuple[tuple[str, str, str, str], ...]  # (table, column, ref_table, ref_column)

    def sqlglot_schema(self) -> dict[str, dict[str, str]]:
        return {t: {c.name: c.type or "TEXT" for c in cols} for t, cols in self.tables.items()}

    def describe(self, tables: list[str] | None = None) -> str:
        """Compact DDL-like text for prompts."""
        lines = []
        for name in tables or list(self.tables):
            cols = ", ".join(
                f"{c.name} {c.type}{' PK' if c.primary_key else ''}" for c in self.tables[name]
            )
            lines.append(f"{name}({cols})")
        fks = [
            f"{t}.{c} -> {rt}.{rc}"
            for t, c, rt, rc in self.foreign_keys
            if tables is None or (t in tables and rt in tables)
        ]
        return "\n".join(lines + (["foreign keys: " + "; ".join(fks)] if fks else []))


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)


def introspect(db_path: str | Path) -> SchemaInfo:
    con = connect_readonly(db_path)
    try:
        names = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        ]
        tables, fks = {}, []
        for name in names:
            tables[name] = tuple(
                Column(r[1], r[2], bool(r[5])) for r in con.execute(f"PRAGMA table_info('{name}')")
            )
            fks += [
                (name, r[3], r[2], r[4]) for r in con.execute(f"PRAGMA foreign_key_list('{name}')")
            ]
        return SchemaInfo(tables, tuple(fks))
    finally:
        con.close()
