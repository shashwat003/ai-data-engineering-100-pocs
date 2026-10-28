"""Thin, safe wrapper around the `opa` binary plus a pure-Python reference of the same policy.

The reference exists for differential testing: if Rego and Python disagree on any generated input,
one of them is wrong. OPA is invoked without a shell, with the request on stdin, and a timeout.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

POLICY = Path(__file__).resolve().parent.parent / "policy" / "data_access.rego"
LEVELS = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
ROLE_ACTIONS = {
    "analyst": {"read"},
    "engineer": {"read", "write"},
    "steward": {"read", "write", "grant"},
    "auditor": {"read"},
}
PII_PURPOSES = {"fraud_investigation", "customer_support", "regulatory_audit"}


class OpaMissing(RuntimeError):
    pass


def opa_bin() -> str:
    b = os.environ.get("OPA_BIN") or shutil.which("opa")
    if not b:
        raise OpaMissing("opa binary not found (set OPA_BIN or install OPA v1.x)")
    return b


def opa_version() -> str:
    out = subprocess.run([opa_bin(), "version"], capture_output=True, text=True, timeout=20)
    return out.stdout.splitlines()[0].replace("Version:", "").strip()


def decide(request: dict, policy: Path = POLICY) -> dict:
    """Evaluate data.dataaccess.decision for one request."""
    r = subprocess.run(
        [
            opa_bin(),
            "eval",
            "-d",
            str(policy),
            "--stdin-input",
            "-f",
            "json",
            "data.dataaccess.decision",
        ],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(f"opa eval failed: {r.stderr.strip()[:300]}")
    res = json.loads(r.stdout).get("result")
    if not res:
        raise RuntimeError("policy returned no decision")
    return res[0]["expressions"][0]["value"]


def decide_many(requests: list[dict], policy: Path = POLICY) -> list[dict]:
    """Batch evaluation in one opa process (a list input keeps differential tests fast)."""
    out = []
    for chunk in range(0, len(requests), 200):
        part = requests[chunk : chunk + 200]
        r = subprocess.run(
            [
                opa_bin(),
                "eval",
                "-d",
                str(policy),
                "--stdin-input",
                "-f",
                "json",
                "[x | some req in input; x := data.dataaccess.decision with input as req]",
            ],  # fmt: skip
            input=json.dumps(part),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if r.returncode != 0:
            raise RuntimeError(f"opa eval failed: {r.stderr.strip()[:300]}")
        out += json.loads(r.stdout)["result"][0]["expressions"][0]["value"]
    return out


def reference(req: dict) -> dict:
    """Independent Python implementation of the intended policy (fail-closed on missing data)."""
    u, res, ctx = req.get("user") or {}, req.get("resource") or {}, req.get("context") or {}
    action = req.get("action")
    reasons: set[str] = set()
    if u.get("active") is not True:
        reasons.add("user_inactive")
    if res.get("classification") not in LEVELS:
        reasons.add("unknown_classification")
    if (
        not (res.get("region") is not None and res.get("region") == u.get("region"))
        and u.get("cross_region_approved") is not True
    ):
        reasons.add("region_mismatch")
    ulevel = LEVELS.get(u.get("clearance"), -1)
    rlevel = LEVELS.get(res.get("classification"), 99)
    if rlevel > ulevel:
        reasons.add("clearance_too_low")
    if res.get("pii") is not False and ctx.get("purpose") not in PII_PURPOSES:
        reasons.add("pii_requires_purpose")
    if rlevel >= LEVELS["confidential"] and ctx.get("mfa") is not True:
        reasons.add("mfa_required")
    roles = u.get("roles") or []
    if "auditor" in roles and action != "read":
        reasons.add("auditors_are_read_only")
    if action in {"write", "grant"} and not (
        res.get("owner_dept") is not None and res.get("owner_dept") == u.get("department")
    ):
        reasons.add("write_outside_own_department")
    rbac = any(action in ROLE_ACTIONS.get(r, set()) for r in roles)
    if not rbac:
        reasons.add("no_role_grants_action")
    return {"allow": rbac and not (reasons - {"no_role_grants_action"}), "reasons": sorted(reasons)}
