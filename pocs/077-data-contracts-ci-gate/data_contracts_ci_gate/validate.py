"""Validate a dataset (CSV or JSON lines) against a contract and report violations."""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .contract import Column, Contract

EXAMPLES_PER_RULE = 3
_INT = re.compile(r"^[+-]?\d+$")
_COMPARE = {
    ">=": lambda a, b: a >= b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
}  # fmt: skip


@dataclass
class Violation:
    rule: str
    severity: str
    column: str | None
    count: int
    message: str
    rows: list[int] = field(default_factory=list)  # 1-based data row numbers (first few)
    examples: list[str] = field(default_factory=list)  # never populated for PII columns


@dataclass
class Report:
    contract: str
    version: str
    rows: int
    violations: list[Violation]

    @property
    def errors(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "error"]

    @property
    def passed(self) -> bool:
        return not self.errors

    def counts(self) -> dict[str, int]:
        return {f"{v.rule}:{v.column}" if v.column else v.rule: v.count for v in self.violations}


def parse_value(raw: Any, col_type: str) -> tuple[bool, Any]:
    """Return (is_valid, parsed). None/empty means null and is handled by the caller."""
    try:
        if col_type == "string":
            return isinstance(raw, str), raw
        if col_type == "integer":
            if isinstance(raw, bool):
                return False, None
            if isinstance(raw, int):
                return True, raw
            if isinstance(raw, str) and _INT.match(raw.strip()):
                return True, int(raw)
            return False, None
        if col_type == "number":
            if isinstance(raw, bool):
                return False, None
            value = float(raw)
            return (not (math.isnan(value) or math.isinf(value))), value
        if col_type == "boolean":
            if isinstance(raw, bool):
                return True, raw
            mapping = {"true": True, "false": False, "1": True, "0": False}
            key = str(raw).strip().lower()
            return key in mapping, mapping.get(key)
        if col_type == "date":
            return True, date.fromisoformat(str(raw).strip())
        if col_type == "timestamp":
            return True, datetime.fromisoformat(str(raw).strip())
    except (ValueError, TypeError):
        pass
    return False, None


def read_rows(path: Path, fmt: str) -> tuple[list[str], list[dict[str, Any]]]:
    if fmt == "csv":
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            header = list(reader.fieldnames or [])
            rows = [{k: (v if v != "" else None) for k, v in row.items()} for row in reader]
        return header, rows
    rows = []
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, start=1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}: line {n} is not valid JSON") from exc
    header = list(dict.fromkeys(k for row in rows for k in row))
    return header, rows


class _Collector:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str | None], Violation] = {}

    def add(self, rule, severity, column, message, row, example=None) -> None:
        v = self.items.setdefault((rule, column), Violation(rule, severity, column, 0, message))
        v.count += 1
        if len(v.rows) < EXAMPLES_PER_RULE:
            v.rows.append(row)
            if example is not None:
                v.examples.append(str(example)[:60])

    def add_dataset(self, rule, severity, column, message) -> None:
        self.items[(rule, column)] = Violation(rule, severity, column, 1, message)


def _as_date(value: Any) -> date:
    return value.date() if isinstance(value, datetime) else value


def validate_dataset(contract: Contract, path: str | Path, *, as_of: date | None = None) -> Report:
    header, rows = read_rows(Path(path), contract.format)
    out = _Collector()
    declared = {c.name: c for c in contract.columns}

    for name in declared:
        if name not in header:
            out.add_dataset("schema.missing_column", "error", name, f"column {name!r} is missing")
    extras = [h for h in header if h not in declared]
    if extras and contract.extra_columns != "allow":
        sev = "error" if contract.extra_columns == "error" else "warn"
        out.add_dataset(
            "schema.extra_column", sev, ",".join(extras), f"undeclared column(s) {extras}"
        )

    parsed: dict[str, list[Any]] = {}  # column -> parsed values per row (None = null/invalid)
    for col in contract.columns:
        if col.name not in header:
            continue
        values: list[Any] = []
        for i, row in enumerate(rows, start=1):
            raw = row.get(col.name)
            if raw is None or (isinstance(raw, str) and raw == ""):
                values.append(None)
                if col.required:
                    out.add("column.required", "error", col.name, f"{col.name} is null", i)
                continue
            ok, value = parse_value(raw, col.type)
            if not ok:
                values.append(None)
                out.add("column.type", "error", col.name, f"{col.name} is not a valid {col.type}",
                        i, None if col.pii else raw)  # fmt: skip
                continue
            values.append(value)
            _check_value(out, col, value, raw, i)
        parsed[col.name] = values
        if col.unique:
            seen: Counter = Counter()
            for i, v in enumerate(values, start=1):
                if v is None:
                    continue
                if seen[v]:
                    out.add("column.unique", "error", col.name, f"{col.name} has repeated values",
                            i, None if col.pii else v)  # fmt: skip
                seen[v] += 1

    n = len(rows)
    _dataset_rules(out, contract, parsed, rows, n, as_of)
    violations = sorted(
        out.items.values(), key=lambda v: (v.severity != "error", v.rule, v.column or "")
    )
    return Report(contract.id, contract.version, n, violations)


def _check_value(out: _Collector, col: Column, value: Any, raw: Any, row: int) -> None:
    shown = None if col.pii else raw
    if col.enum is not None and raw not in col.enum and str(raw) not in map(str, col.enum):
        out.add("column.enum", "error", col.name, f"{col.name} not in allowed values", row, shown)
    if col.min is not None and isinstance(value, (int, float)) and value < col.min:
        out.add("column.min", "error", col.name, f"{col.name} below minimum {col.min}", row, shown)
    if col.max is not None and isinstance(value, (int, float)) and value > col.max:
        out.add("column.max", "error", col.name, f"{col.name} above maximum {col.max}", row, shown)
    if col.pattern is not None and isinstance(value, str) and not re.search(col.pattern, value):
        out.add(
            "column.pattern", "error", col.name, f"{col.name} does not match pattern", row, shown
        )
    if col.max_length is not None and isinstance(value, str) and len(value) > col.max_length:
        out.add(
            "column.max_length",
            "error",
            col.name,
            f"{col.name} longer than {col.max_length}",
            row,
            shown,
        )


def _dataset_rules(out, contract: Contract, parsed, rows, n: int, as_of: date | None) -> None:
    if contract.min_rows is not None and n < contract.min_rows:
        out.add_dataset(
            "quality.min_rows", "error", None, f"{n} rows < minimum {contract.min_rows}"
        )
    if contract.max_rows is not None and n > contract.max_rows:
        out.add_dataset(
            "quality.max_rows", "error", None, f"{n} rows > maximum {contract.max_rows}"
        )
    for col, limit in contract.max_null_rate:
        if col in parsed and n:
            rate = sum(v is None for v in parsed[col]) / n
            if rate > limit:
                out.add_dataset("quality.max_null_rate", "error", col,
                                f"null rate {rate:.3f} > allowed {limit}")  # fmt: skip
    if contract.freshness and contract.freshness.column in parsed:
        dates = [_as_date(v) for v in parsed[contract.freshness.column] if v is not None]
        if as_of is None:
            raise ValueError("a freshness rule needs an as_of date")
        newest = max(dates, default=None)
        if newest is None or newest < as_of - timedelta(days=contract.freshness.max_age_days):
            out.add_dataset("quality.freshness", "error", contract.freshness.column,
                            f"newest {newest} is older than {contract.freshness.max_age_days} days "
                            f"before {as_of}")  # fmt: skip
    for rule in contract.rules:
        if rule.type == "column_compare":
            left, right, op = rule.param("left"), rule.param("right"), rule.param("op")
            if left not in parsed or right not in parsed:
                continue
            for i, (a, b) in enumerate(zip(parsed[left], parsed[right], strict=True), start=1):
                if a is not None and b is not None and not _COMPARE[op](_as_date(a), _as_date(b)):
                    out.add("quality.column_compare", rule.severity, f"{left}{op}{right}",
                            f"expected {left} {op} {right}", i)  # fmt: skip
        elif rule.type == "unique_together":
            cols = rule.param("columns")
            if any(c not in parsed for c in cols):
                continue
            seen: set = set()
            for i, key in enumerate(zip(*(parsed[c] for c in cols), strict=True), start=1):
                if None in key:
                    continue
                if key in seen:
                    out.add("quality.unique_together", rule.severity, "+".join(cols),
                            f"duplicate combination of {cols}", i)  # fmt: skip
                seen.add(key)
