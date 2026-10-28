from __future__ import annotations

from pathlib import Path

import yaml

from .rules import (
    DEEPFAKE_OBLIGATION,
    HIGH_RISK_DOMAINS,
    HIGH_RISK_OBLIGATIONS,
    LIMITED_OBLIGATIONS,
    PROHIBITED_DOMAINS,
)

REQUIRED = ("id", "name", "purpose")
KNOWN_KEYS = {
    "id", "name", "purpose", "domains", "affects_natural_persons", "interacts_with_humans",
    "generates_content", "deepfake", "autonomy", "controls",
}  # fmt: skip
ALL_CONTROLS = set(HIGH_RISK_OBLIGATIONS) | set(LIMITED_OBLIGATIONS) | set(DEEPFAKE_OBLIGATION)


def load_inventory(path: str | Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text())  # safe_load only: inventories are untrusted input
    if not isinstance(data, list):
        raise ValueError("inventory must be a YAML list of systems")
    seen = set()
    for s in data:
        if not isinstance(s, dict):
            raise ValueError("each system must be a mapping")
        miss = [k for k in REQUIRED if not s.get(k)]
        if miss:
            raise ValueError(f"system {s.get('id', '?')}: missing {miss}")
        unknown = set(s) - KNOWN_KEYS
        if unknown:
            raise ValueError(f"system {s['id']}: unknown fields {sorted(unknown)}")
        if s["id"] in seen:
            raise ValueError(f"duplicate id {s['id']}")
        seen.add(s["id"])
        bad = (
            set(s.get("domains") or [])
            - set(PROHIBITED_DOMAINS)
            - set(HIGH_RISK_DOMAINS)
            - {"none"}
        )
        if bad:
            raise ValueError(f"system {s['id']}: unknown domain(s) {sorted(bad)}")
    return data


def classify(s: dict) -> dict:
    """Return tier, the rules that fired and the obligations with evidence status."""
    doms = s.get("domains") or []
    reasons: list[str] = []
    obligations: dict[str, str] = {}
    tier = "minimal"
    for d in doms:
        if d in PROHIBITED_DOMAINS:
            tier = "prohibited"
            reasons.append(PROHIBITED_DOMAINS[d])
    if tier != "prohibited":
        for d in doms:
            if d in HIGH_RISK_DOMAINS:
                tier = "high"
                reasons.append(HIGH_RISK_DOMAINS[d])
        if tier == "high":
            obligations.update(HIGH_RISK_OBLIGATIONS)
        else:
            if s.get("interacts_with_humans"):
                obligations.update(LIMITED_OBLIGATIONS)
                reasons.append("Art. 50: system interacts directly with people")
            if s.get("deepfake"):
                obligations.update(DEEPFAKE_OBLIGATION)
                reasons.append("Art. 50: deepfake content")
            if obligations:
                tier = "limited"
    have = set(s.get("controls") or [])
    status = {k: ("evidenced" if k in have else "MISSING") for k in obligations}
    gaps = [k for k, v in status.items() if v == "MISSING"]
    out = {
        "id": s["id"], "name": s["name"], "purpose": s["purpose"], "tier": tier,
        "reasons": reasons or ["no prohibited, high-risk or transparency rule matched"],
        "obligations": {k: {"label": obligations[k], "status": v} for k, v in status.items()},
        "gaps": gaps,
        "unmapped_controls": sorted(have - ALL_CONTROLS),
    }  # fmt: skip
    out["action"] = (
        "STOP: do not deploy; escalate to legal"
        if tier == "prohibited"
        else "close gaps before go-live"
        if gaps
        else "no open gaps against modelled obligations"
    )
    return out


def summarize(items: list[dict]) -> dict:
    tiers = {t: 0 for t in ("prohibited", "high", "limited", "minimal")}
    for i in items:
        tiers[i["tier"]] += 1
    return {
        "systems": len(items),
        "by_tier": tiers,
        "open_gaps": sum(len(i["gaps"]) for i in items),
    }
