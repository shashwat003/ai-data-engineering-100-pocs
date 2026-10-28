"""Data contract model and strict YAML loader.

A contract describes one dataset: columns (type, nullability, uniqueness, enum, range, pattern,
PII flag) plus dataset-level quality rules. The format is deliberately small and is *inspired by*
open data-contract specifications; it is not ODCS-compliant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

TYPES = ("string", "integer", "number", "boolean", "date", "timestamp")
FORMATS = ("csv", "jsonl")
EXTRA_POLICIES = ("error", "warn", "allow")
SEVERITIES = ("error", "warn")
RULE_TYPES = {"column_compare": {"left", "op", "right"}, "unique_together": {"columns"}}
COMPARE_OPS = (">=", ">", "<=", "<", "==", "!=")
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

_COLUMN_KEYS = {"name", "type", "required", "unique", "pii", "enum", "min", "max", "pattern",
                "max_length", "description"}  # fmt: skip
_TOP_KEYS = {
    "contract",
    "version",
    "owner",
    "description",
    "format",
    "extra_columns",
    "columns",
    "quality",
}
_QUALITY_KEYS = {"min_rows", "max_rows", "max_null_rate", "freshness", "rules"}


class ContractError(ValueError):
    """The contract file is malformed. The message lists every problem found."""


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    required: bool = False
    unique: bool = False
    pii: bool = False
    enum: tuple | None = None
    min: float | None = None
    max: float | None = None
    pattern: str | None = None
    max_length: int | None = None
    description: str = ""


@dataclass(frozen=True)
class Rule:
    type: str
    severity: str
    params: tuple[tuple[str, Any], ...]  # sorted, hashable

    def param(self, key: str) -> Any:
        return dict(self.params)[key]


@dataclass(frozen=True)
class Freshness:
    column: str
    max_age_days: int


@dataclass(frozen=True)
class Contract:
    id: str
    version: str
    owner: str
    description: str
    format: str
    extra_columns: str
    columns: tuple[Column, ...]
    min_rows: int | None = None
    max_rows: int | None = None
    max_null_rate: tuple[tuple[str, float], ...] = ()
    freshness: Freshness | None = None
    rules: tuple[Rule, ...] = field(default_factory=tuple)

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


def parse_semver(version: str) -> tuple[int, int, int]:
    m = SEMVER.match(str(version))
    if not m:
        raise ContractError(f"version {version!r} is not MAJOR.MINOR.PATCH")
    return int(m[1]), int(m[2]), int(m[3])


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_contract(text: str) -> Contract:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ContractError(f"invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ContractError("contract must be a mapping")
    errors: list[str] = []

    def check_keys(where: str, data: dict, allowed: set[str]) -> None:
        for key in sorted(set(data) - allowed):
            errors.append(f"{where}: unknown key {key!r}")

    check_keys("contract", raw, _TOP_KEYS)
    for key in ("contract", "version", "owner", "columns"):
        if key not in raw:
            errors.append(f"missing required key {key!r}")
    try:
        parse_semver(str(raw.get("version", "")))
    except ContractError as exc:
        errors.append(str(exc))
    if raw.get("format", "csv") not in FORMATS:
        errors.append(f"format must be one of {FORMATS}")
    if raw.get("extra_columns", "warn") not in EXTRA_POLICIES:
        errors.append(f"extra_columns must be one of {EXTRA_POLICIES}")

    columns, seen = [], set()
    for i, c in enumerate(raw.get("columns") or []):
        where = f"columns[{i}]"
        if not isinstance(c, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        check_keys(where, c, _COLUMN_KEYS)
        name = c.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{where}: name is required")
            continue
        if name in seen:
            errors.append(f"{where}: duplicate column {name!r}")
        seen.add(name)
        if c.get("type") not in TYPES:
            errors.append(f"{name}: type must be one of {TYPES}")
        for flag in ("required", "unique", "pii"):
            if not isinstance(c.get(flag, False), bool):
                errors.append(f"{name}: {flag} must be true/false")
        for bound in ("min", "max"):
            if bound in c and not _number(c[bound]):
                errors.append(f"{name}: {bound} must be a number")
        if "max_length" in c and (not isinstance(c["max_length"], int) or c["max_length"] < 1):
            errors.append(f"{name}: max_length must be a positive integer")
        if "enum" in c and (not isinstance(c["enum"], list) or not c["enum"]):
            errors.append(f"{name}: enum must be a non-empty list")
        if "pattern" in c:
            try:
                re.compile(c["pattern"])
            except (re.error, TypeError) as exc:
                errors.append(f"{name}: invalid pattern: {exc}")
        if c.get("type") in ("string", "boolean", "date", "timestamp") and (
            "min" in c or "max" in c
        ):
            errors.append(f"{name}: min/max apply to numeric columns only")
        if c.get("type") in ("integer", "number", "boolean") and (
            "pattern" in c or "max_length" in c
        ):
            errors.append(f"{name}: pattern/max_length apply to text-like columns only")
        columns.append(
            Column(
                name=name,
                type=c.get("type", "string"),
                required=bool(c.get("required", False)),
                unique=bool(c.get("unique", False)),
                pii=bool(c.get("pii", False)),
                enum=tuple(c["enum"]) if isinstance(c.get("enum"), list) else None,
                min=c.get("min"),
                max=c.get("max"),
                pattern=c.get("pattern"),
                max_length=c.get("max_length"),
                description=str(c.get("description", "")),
            )
        )

    quality = raw.get("quality") or {}
    if not isinstance(quality, dict):
        errors.append("quality must be a mapping")
        quality = {}
    check_keys("quality", quality, _QUALITY_KEYS)
    for key in ("min_rows", "max_rows"):
        if key in quality and (not isinstance(quality[key], int) or quality[key] < 0):
            errors.append(f"quality.{key} must be a non-negative integer")
    null_rates = quality.get("max_null_rate") or {}
    for col, rate in null_rates.items():
        if col not in seen:
            errors.append(f"quality.max_null_rate: unknown column {col!r}")
        if not _number(rate) or not 0 <= rate <= 1:
            errors.append(f"quality.max_null_rate.{col}: must be between 0 and 1")
    fresh = quality.get("freshness")
    freshness = None
    if fresh is not None:
        if not isinstance(fresh, dict) or set(fresh) != {"column", "max_age_days"}:
            errors.append("quality.freshness needs exactly: column, max_age_days")
        else:
            col = next((c for c in columns if c.name == fresh["column"]), None)
            if col is None or col.type not in ("date", "timestamp"):
                errors.append("quality.freshness.column must be a date/timestamp column")
            elif not isinstance(fresh["max_age_days"], int) or fresh["max_age_days"] < 0:
                errors.append("quality.freshness.max_age_days must be a non-negative integer")
            else:
                freshness = Freshness(fresh["column"], fresh["max_age_days"])
    rules = []
    for i, r in enumerate(quality.get("rules") or []):
        where = f"quality.rules[{i}]"
        if not isinstance(r, dict) or r.get("type") not in RULE_TYPES:
            errors.append(f"{where}: type must be one of {sorted(RULE_TYPES)}")
            continue
        severity = r.get("severity", "error")
        if severity not in SEVERITIES:
            errors.append(f"{where}: severity must be one of {SEVERITIES}")
        params = {k: v for k, v in r.items() if k not in ("type", "severity")}
        if set(params) != RULE_TYPES[r["type"]]:
            errors.append(f"{where}: {r['type']} needs exactly {sorted(RULE_TYPES[r['type']])}")
            continue
        used = (
            [params["left"], params["right"]]
            if r["type"] == "column_compare"
            else params["columns"]
        )
        if not isinstance(used, list):
            used = list(used) if isinstance(used, tuple) else [used]
        for col in used:
            if col not in seen:
                errors.append(f"{where}: unknown column {col!r}")
        if r["type"] == "column_compare" and params["op"] not in COMPARE_OPS:
            errors.append(f"{where}: op must be one of {COMPARE_OPS}")
        if r["type"] == "unique_together":
            params["columns"] = tuple(params["columns"])
        rules.append(Rule(r["type"], severity, tuple(sorted(params.items()))))

    if errors:
        raise ContractError("; ".join(errors))
    return Contract(
        id=str(raw["contract"]),
        version=str(raw["version"]),
        owner=str(raw["owner"]),
        description=str(raw.get("description", "")),
        format=raw.get("format", "csv"),
        extra_columns=raw.get("extra_columns", "warn"),
        columns=tuple(columns),
        min_rows=quality.get("min_rows"),
        max_rows=quality.get("max_rows"),
        max_null_rate=tuple(sorted((k, float(v)) for k, v in null_rates.items())),
        freshness=freshness,
        rules=tuple(rules),
    )


def load_contract(path: str | Path) -> Contract:
    try:
        return parse_contract(Path(path).read_text())
    except ContractError as exc:
        raise ContractError(f"{path}: {exc}") from exc
