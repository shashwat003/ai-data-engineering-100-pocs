"""Least-privilege lint over a catalog's grants and tags."""

from __future__ import annotations

from .model import SUPER, Catalog
from .policy import effective_access

BROAD = {"*", "everyone", "public"}


def lint(
    cat: Catalog, pii_roles: set[str], pii_tag: tuple[str, str] = ("sensitivity", "pii")
) -> list[str]:
    out = []
    for g in cat.grants:
        if g.principal == SUPER:
            out.append(
                f"grant to {SUPER} exposes data to every IAM-authorised principal (legacy open access)"
            )
        if g.principal.lower() in BROAD:
            out.append(f"grant to broad principal {g.principal!r}")
        if g.database and "SELECT" in g.permissions and g.principal not in pii_roles:
            out.append(
                f"{g.principal}: SELECT on whole database {g.database} (prefer tag or table grants)"
            )
        if g.grant_option and g.principal not in pii_roles:
            out.append(f"{g.principal}: grant option lets it delegate access onward")
        if g.tag_expression:
            ok = any(
                cat.matches(g.tag_expression, cat.effective_column_tags(t, c.name))
                for t, tbl in cat.tables.items()
                for c in tbl.columns
            )
            if not ok:
                out.append(
                    f"{g.principal}: tag expression matches no column (dead grant, or a retagging silently removed access)"
                )
    # PII reachable by principals not cleared for it
    for p in {g.principal for g in cat.grants}:
        if p in pii_roles or p == SUPER:
            continue
        for t, tbl in cat.tables.items():
            exposed = effective_access(cat, p, t).columns
            for c in tbl.columns:
                if (
                    c.name in exposed
                    and cat.effective_column_tags(t, c.name).get(pii_tag[0]) == pii_tag[1]
                ):
                    out.append(f"{p} can read PII column {t}.{c.name} without clearance")
    used = {k for g in cat.grants for k, _ in (g.tag_expression or ())}
    for k in cat.tag_defs:
        if k not in used:
            out.append(f"LF-tag {k!r} is defined but never used in a grant")
    return sorted(set(out))
