"""Named access scenarios with the expected decision, plus a seeded random request generator."""

from __future__ import annotations

import random

USER = {
    "active": True, "roles": ["analyst"], "department": "risk", "region": "eu",
    "clearance": "confidential", "cross_region_approved": False,
}  # fmt: skip
RES = {"classification": "internal", "owner_dept": "risk", "region": "eu", "pii": False}
CTX = {"purpose": "customer_support", "mfa": True}


def req(user=None, res=None, action="read", ctx=None) -> dict:
    return {
        "user": {**USER, **(user or {})},
        "resource": {**RES, **(res or {})},
        "action": action,
        "context": {**CTX, **(ctx or {})},
    }


# (name, request, expected allow, expected reasons)
SCENARIOS = [
    ("analyst reads internal data", req(), True, []),
    ("analyst cannot write", req(action="write"), False, ["no_role_grants_action"]),
    ("engineer writes own dept", req({"roles": ["engineer"]}, action="write"), True, []),
    (
        "engineer writes other dept",
        req({"roles": ["engineer"]}, {"owner_dept": "hr"}, "write"),
        False,
        ["write_outside_own_department"],
    ),
    ("steward grants own dept", req({"roles": ["steward"]}, action="grant"), True, []),
    (
        "PII without approved purpose",
        req(res={"pii": True}, ctx={"purpose": "marketing"}),
        False,
        ["pii_requires_purpose"],
    ),
    ("PII with approved purpose", req(res={"pii": True}, ctx={"purpose": "fraud_investigation"}), True, []),
    (
        "restricted data above clearance",
        req(res={"classification": "restricted"}),
        False,
        ["clearance_too_low"],
    ),
    ("confidential without MFA", req(res={"classification": "confidential"}, ctx={"mfa": False}), False, ["mfa_required"]),
    ("cross-region read denied", req(res={"region": "us"}), False, ["region_mismatch"]),
    ("cross-region read with approval", req({"cross_region_approved": True}, {"region": "us"}), True, []),
    ("inactive (departed) user", req({"active": False}), False, ["user_inactive"]),
    ("auditor reads", req({"roles": ["auditor"]}), True, []),
    (
        "auditor + engineer role cannot write (auditor wins)",
        req({"roles": ["auditor", "engineer"]}, action="write"),
        False,
        ["auditors_are_read_only"],
    ),
    ("unknown classification", req(res={"classification": "top-secret"}), False, ["clearance_too_low", "unknown_classification"]),
    ("no roles at all", req({"roles": []}), False, ["no_role_grants_action"]),
]  # fmt: skip


def random_requests(n: int, seed: int = 5) -> list[dict]:
    """Requests built from a small value space so every deny reason is hit many times."""
    rng = random.Random(seed)
    roles_pool = ["analyst", "engineer", "steward", "auditor", "intern"]
    out = []
    for _ in range(n):
        out.append(
            {
                "user": {
                    "active": rng.random() > 0.1,
                    "roles": rng.sample(roles_pool, rng.randint(0, 2)),
                    "department": rng.choice(["risk", "hr"]),
                    "region": rng.choice(["eu", "us"]),
                    "clearance": rng.choice(
                        ["public", "internal", "confidential", "restricted", "x"]
                    ),
                    "cross_region_approved": rng.random() > 0.8,
                },
                "resource": {
                    "classification": rng.choice(
                        ["public", "internal", "confidential", "restricted", "weird"]
                    ),
                    "owner_dept": rng.choice(["risk", "hr"]),
                    "region": rng.choice(["eu", "us"]),
                    "pii": rng.random() > 0.5,
                },
                "action": rng.choice(["read", "write", "grant", "delete"]),
                "context": {
                    "purpose": rng.choice(["customer_support", "marketing", None]),
                    "mfa": rng.random() > 0.3,
                },
            }
        )
    return out
