"""Detectors that compare two dbt project versions and produce anchored review findings."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

import sqlglot

from .project import Model, children

SEV = {"blocker": 3, "high": 2, "warn": 1, "info": 0}
INSTRUCTION = re.compile(
    r"(?i)\b(ignore|disregard)\b.{0,40}\b(findings?|instructions?|rules?|checks?)\b|\bapprove (this|the) (pr|change)\b|\bnote to (the )?(reviewer|ai|bot)\b"
)


@dataclass
class Finding:
    code: str
    severity: str
    category: str  # "risk" (may break or change data) or "hygiene"
    model: str
    file: str
    line: int
    message: str


def norm(m: Model) -> str:
    """Canonical SQL: comments, case and whitespace removed. Equal strings mean no semantic edit."""
    if m.ast is None:
        return m.sql
    return sqlglot.parse_one(m.sql, read="duckdb").sql(
        dialect="duckdb", normalize=True, comments=False
    )


def first_diff_line(a: str, b: str) -> int:
    for tag, _i1, _i2, j1, _j2 in difflib.SequenceMatcher(
        None, a.splitlines(), b.splitlines()
    ).get_opcodes():
        if tag != "equal":
            return j1 + 1
    return 1


def review(base: dict[str, Model], head: dict[str, Model]) -> list[Finding]:
    f: list[Finding] = []
    kids = children(head)
    for name, h in head.items():
        b = base.get(name)
        if h.ast is None:
            f.append(
                Finding(
                    "sql_parse_error",
                    "blocker",
                    "risk",
                    name,
                    h.path,
                    1,
                    "model SQL could not be parsed",
                )
            )
            continue
        for c in h.comments:
            if INSTRUCTION.search(c):
                f.append(
                    Finding(
                        "suspicious_comment",
                        "warn",
                        "hygiene",
                        name,
                        h.path,
                        first_diff_line(b.raw if b else "", h.raw),
                        "SQL comment addresses a reviewer or tool; it was ignored. A human should read it.",
                    )
                )
        if h.hardcoded:
            f.append(
                Finding(
                    "hardcoded_relation",
                    "high",
                    "risk",
                    name,
                    h.path,
                    first_diff_line(b.raw if b else "", h.raw),
                    f"reads {h.hardcoded} without ref(): dbt cannot order or track this dependency",
                )
            )
        if b is None:
            if not h.description:
                f.append(
                    Finding(
                        "new_model_no_description",
                        "warn",
                        "hygiene",
                        name,
                        h.path,
                        1,
                        "new model has no description",
                    )
                )
            if not h.tests:
                f.append(
                    Finding(
                        "new_model_no_tests",
                        "warn",
                        "hygiene",
                        name,
                        h.path,
                        1,
                        "new model has no tests",
                    )
                )
            continue
        line = first_diff_line(b.raw, h.raw)
        removed = [c for c in b.columns if c not in h.columns]
        for c in removed:
            users = [
                k
                for k in kids.get(name, [])
                if c
                in {
                    col.name
                    for col in (head[k].ast.find_all(sqlglot.exp.Column) if head[k].ast else [])
                }
            ]
            if users:
                f.append(
                    Finding(
                        "removed_column_used_downstream",
                        "blocker",
                        "risk",
                        name,
                        h.path,
                        line,
                        f"column `{c}` was removed but is referenced by {users}",
                    )
                )
            else:
                f.append(
                    Finding(
                        "removed_column",
                        "warn",
                        "risk",
                        name,
                        h.path,
                        line,
                        f"column `{c}` was removed; no downstream reference found in this project (external consumers?)",
                    )
                )
        added = [c for c in h.columns if c not in b.columns]
        if added:
            f.append(
                Finding(
                    "added_column", "info", "hygiene", name, h.path, line, f"new column(s) {added}"
                )
            )
        if b.group_by != h.group_by:
            f.append(
                Finding(
                    "grain_change",
                    "high",
                    "risk",
                    name,
                    h.path,
                    line,
                    f"GROUP BY changed from {b.group_by} to {h.group_by}; the row grain differs, check uniqueness tests and consumers",
                )
            )
        if b.joins != h.joins:
            f.append(
                Finding(
                    "join_change",
                    "warn",
                    "risk",
                    name,
                    h.path,
                    line,
                    f"joins changed from {b.joins} to {h.joins}; row count may change",
                )
            )
        if b.where != h.where:
            f.append(
                Finding(
                    "filter_change",
                    "high" if not h.where else "warn",
                    "risk",
                    name,
                    h.path,
                    line,
                    f"WHERE changed from `{b.where or 'none'}` to `{h.where or 'none'}`",
                )
            )
        if b.materialized != h.materialized:
            f.append(
                Finding(
                    "materialization_change",
                    "info",
                    "hygiene",
                    name,
                    h.path,
                    line,
                    f"materialization {b.materialized} -> {h.materialized}",
                )
            )
        for t in sorted(set(b.tests) - set(h.tests)):
            f.append(
                Finding(
                    "test_removed",
                    "warn",
                    "hygiene",
                    name,
                    "models/schema.yml",
                    1,
                    f"test `{t}` was removed",
                )
            )
    for name in base:
        if name not in head:
            users = [k for k in children(base).get(name, []) if k in head]
            f.append(
                Finding(
                    "model_removed",
                    "blocker" if users else "warn",
                    "risk",
                    name,
                    base[name].path,
                    1,
                    f"model deleted; still referenced by {users}" if users else "model deleted",
                )
            )
    return sorted(f, key=lambda x: (-SEV[x.severity], x.model, x.code))


def risk_flag(findings: list[Finding], threshold: str = "warn") -> bool:
    return any(x.category == "risk" and SEV[x.severity] >= SEV[threshold] for x in findings)


def comment_payload(findings: list[Finding]) -> dict:
    """The review a bot would post (NOT posted anywhere here)."""
    verdict = (
        "request_changes"
        if any(x.severity in {"blocker", "high"} for x in findings)
        else "comment"
        if findings
        else "approve"
    )
    return {
        "event": verdict,
        "body": f"{len(findings)} finding(s)",
        "comments": [
            {"path": x.file, "line": x.line, "body": f"[{x.severity}] {x.code}: {x.message}"}
            for x in findings
            if x.severity != "info"
        ],
    }
