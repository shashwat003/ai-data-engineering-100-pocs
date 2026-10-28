"""Incident input, the planner's step types and the structured triage output with validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

ALERT_TYPES = ("freshness_breach", "pipeline_failure", "contract_violation", "row_count_drop",
               "pii_exposure_suspected", "unknown")  # fmt: skip
CATEGORIES = ("transient_failure", "persistent_failure", "upstream_dependency", "schema_change",
              "data_volume_anomaly", "security_exposure", "unknown")  # fmt: skip
ALLOWED_CATEGORIES = {  # which conclusions are plausible for each alert type
    "freshness_breach": {
        "transient_failure",
        "persistent_failure",
        "upstream_dependency",
        "unknown",
    },
    "pipeline_failure": {"transient_failure", "persistent_failure", "unknown"},
    "contract_violation": {"schema_change", "unknown"},
    "row_count_drop": {"data_volume_anomaly", "unknown"},
    "pii_exposure_suspected": {"security_exposure", "unknown"},
    "unknown": set(CATEGORIES),
}
STATUSES = ("resolved", "escalated", "awaiting_approval", "budget_exhausted", "failed")


@dataclass(frozen=True)
class Incident:
    id: str
    alert_type: str
    dataset: str
    pipeline: str | None
    severity: int  # 1 = most severe
    description: str  # free text from the alerting system: UNTRUSTED


@dataclass(frozen=True)
class Step:
    tool: str
    args: dict
    rationale: str = ""


@dataclass(frozen=True)
class Finish:
    category: str
    root_cause: str
    confidence: float
    next_steps: tuple[str, ...] = ()


@dataclass
class Observation:
    tool: str
    args: dict
    outcome: str  # ok | denied | error | pending_approval | rejected | approval_denied
    data: dict | str | None = None
    detail: str = ""


@dataclass
class Triage:
    incident_id: str
    status: str
    category: str
    severity: int
    root_cause: str
    confidence: float
    evidence: list[dict] = field(default_factory=list)  # {"tool", "finding"}
    actions_executed: list[dict] = field(default_factory=list)
    actions_pending_approval: list[dict] = field(default_factory=list)
    actions_blocked: list[dict] = field(default_factory=list)  # denied / rejected / human-denied
    next_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def validate_triage(t: dict) -> list[str]:
    """Return a list of problems (empty = valid). Used on every result before it leaves the agent."""
    errs = []
    if t.get("status") not in STATUSES:
        errs.append("status")
    if t.get("category") not in CATEGORIES:
        errs.append("category")
    if not (isinstance(t.get("severity"), int) and 1 <= t["severity"] <= 4):
        errs.append("severity")
    c = t.get("confidence")
    if not (isinstance(c, (int, float)) and 0 <= c <= 1):
        errs.append("confidence")
    if not (isinstance(t.get("root_cause"), str) and 0 < len(t["root_cause"]) <= 500):
        errs.append("root_cause")
    for key in (
        "evidence",
        "actions_executed",
        "actions_pending_approval",
        "actions_blocked",
        "next_steps",
    ):
        if not isinstance(t.get(key), list):
            errs.append(key)
    if not isinstance(t.get("incident_id"), str):
        errs.append("incident_id")
    return errs
