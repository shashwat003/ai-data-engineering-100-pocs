from __future__ import annotations

from pocs_common import markdown_table

from .evolution import EvolutionResult
from .validate import Report


def report_markdown(report: Report) -> str:
    head = f"`{report.contract}` v{report.version}: {report.rows} rows, " + (
        "**PASSED**" if report.passed else f"**FAILED** ({len(report.errors)} error rule(s))"
    )
    if not report.violations:
        return head
    rows = [
        [v.severity, v.rule, v.column or "-", v.count, ", ".join(map(str, v.rows)) or "-",
         ", ".join(v.examples) or "-"]
        for v in report.violations
    ]  # fmt: skip
    return (
        head
        + "\n\n"
        + markdown_table(["severity", "rule", "column", "count", "first rows", "examples"], rows)
    )


def evolution_markdown(old_version: str, new_version: str, result: EvolutionResult) -> str:
    verdict = "OK" if result.ok else "REJECTED: " + "; ".join(result.messages)
    head = (f"{old_version} -> {new_version}: required bump **{result.required_bump}**, "
            f"actual **{result.actual_bump}** => {verdict}")  # fmt: skip
    if not result.changes:
        return head
    rows = [[c.level, c.kind, c.target, c.detail] for c in result.changes]
    return head + "\n\n" + markdown_table(["level", "change", "target", "detail"], rows)
