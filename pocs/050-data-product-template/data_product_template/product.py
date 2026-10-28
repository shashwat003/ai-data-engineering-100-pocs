"""Data product manifest: ownership, ports, contract reference, SLOs, access and lifecycle. Strict YAML loader."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml


class ProductError(ValueError):
    pass


NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
STATUSES = ("draft", "active", "deprecated")
TIERS = ("gold", "silver", "bronze")
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")
TOP = {
    "product",
    "domain",
    "owner",
    "tier",
    "status",
    "sunset_date",
    "inputs",
    "output",
    "contract",
    "slos",
    "access",
    "retention_days",
    "consumers",
}
SLO_KEYS = {
    "freshness_hours",
    "completeness_min",
    "validity_min",
    "volume_tolerance_pct",
    "availability_target",
    "window_days",
}


@dataclass(frozen=True)
class Slos:
    freshness_hours: float
    completeness_min: float
    validity_min: float
    volume_tolerance_pct: float
    availability_target: float
    window_days: int


@dataclass(frozen=True)
class Product:
    name: str
    domain: str
    owner_team: str
    oncall: str
    tier: str
    status: str
    sunset_date: date | None
    inputs: tuple[str, ...]
    output_table: str
    contract: str
    slos: Slos
    classification: str
    allowed_roles: tuple[str, ...]
    retention_days: int
    consumers: tuple[str, ...] = field(default_factory=tuple)


def _num(v, lo, hi, what, errs):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        errs.append(f"{what} must be a number in [{lo}, {hi}]")
        return lo
    return v


def parse_product(text: str) -> Product:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ProductError(f"invalid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise ProductError("product file must be a mapping")
    errs: list[str] = []
    unknown = set(raw) - TOP
    if unknown:
        errs.append(f"unknown top-level keys {sorted(unknown)}")
    name = str(raw.get("product", ""))
    if not NAME.match(name):
        errs.append("product must be snake_case, 3-41 characters")
    owner = raw.get("owner") or {}
    if not isinstance(owner, dict) or not owner.get("team") or not owner.get("oncall"):
        errs.append("owner needs team and oncall")
        owner = {"team": "", "oncall": ""}
    if raw.get("tier") not in TIERS:
        errs.append(f"tier must be one of {TIERS}")
    status = raw.get("status", "draft")
    if status not in STATUSES:
        errs.append(f"status must be one of {STATUSES}")
    sunset = raw.get("sunset_date")
    if status == "deprecated" and not isinstance(sunset, date):
        errs.append("deprecated products need a sunset_date")
    if status != "deprecated" and sunset is not None:
        errs.append("sunset_date is only valid for deprecated products")
    s = raw.get("slos") or {}
    if not isinstance(s, dict) or set(s) != SLO_KEYS:
        errs.append(f"slos must define exactly {sorted(SLO_KEYS)}")
        s = {k: 0 for k in SLO_KEYS}
    slos = Slos(
        _num(s["freshness_hours"], 0.1, 24 * 30, "freshness_hours", errs), _num(s["completeness_min"], 0.5, 1, "completeness_min", errs), _num(s["validity_min"], 0.5, 1, "validity_min", errs),
        _num(s["volume_tolerance_pct"], 1, 100, "volume_tolerance_pct", errs), _num(s["availability_target"], 0.5, 0.99999, "availability_target", errs), int(_num(s["window_days"], 1, 90, "window_days", errs)),
    )  # fmt: skip
    access = raw.get("access") or {}
    if access.get("classification") not in CLASSIFICATIONS:
        errs.append(f"access.classification must be one of {CLASSIFICATIONS}")
    roles = access.get("allowed_roles") or []
    if not roles or not all(isinstance(r, str) for r in roles):
        errs.append("access.allowed_roles must be a non-empty list of strings")
    out = raw.get("output") or {}
    if not out.get("table"):
        errs.append("output.table is required")
    if not raw.get("contract"):
        errs.append("contract path is required")
    ret = raw.get("retention_days")
    if isinstance(ret, bool) or not isinstance(ret, int) or not 1 <= ret <= 3650:
        errs.append("retention_days must be an integer in [1, 3650]")
        ret = 1
    if raw.get("tier") == "gold" and slos.availability_target < 0.99:
        errs.append("gold products need availability_target >= 0.99")
    if access.get("classification") in ("confidential", "restricted") and "analyst" in roles:
        errs.append("analyst role cannot be granted confidential or restricted products")
    if errs:
        raise ProductError("; ".join(errs))
    return Product(name, str(raw.get("domain", "")), owner["team"], owner["oncall"], raw["tier"], status, sunset, tuple(raw.get("inputs") or ()), out["table"], raw["contract"], slos,
                   access["classification"], tuple(roles), ret, tuple(raw.get("consumers") or ()))  # fmt: skip


def load_product(path: str | Path) -> Product:
    return parse_product(Path(path).read_text())
