"""Guardrail regression matrix: hand-written expectations (never generated from the simulator) plus mutation checks."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from .simulator import Organisation, Request

CASES = Path(__file__).resolve().parent / "cases.json"


def load_cases() -> list[dict]:
    return json.loads(CASES.read_text())


def to_request(c: dict) -> Request:
    p = c["principal"]
    arn = f"arn:aws:iam::{c['account']}:{p}" if p != "root" else f"arn:aws:iam::{c['account']}:root"
    return Request(
        arn,
        c["account"],
        c["action"],
        c.get("resource", "*"),
        tuple(sorted(c.get("context", {}).items())),
    )


def run_matrix(org: Organisation, cases: list[dict] | None = None) -> list[dict]:
    out = []
    for c in cases or load_cases():
        d = org.evaluate(to_request(c))
        out.append(
            {
                "id": c["id"],
                "expect": c["expect"],
                "got": "allow" if d.allowed else "deny",
                "reason": d.reason,
                "ok": ("allow" if d.allowed else "deny") == c["expect"],
            }
        )
    return out


def mutation_report(org: Organisation, cases: list[dict] | None = None) -> dict[str, list[str]]:
    """For each guardrail policy: which cases flip if that policy is detached everywhere. A policy no case detects is untested."""
    base = {r["id"]: r["got"] for r in run_matrix(org, cases)}
    rep: dict[str, list[str]] = {}
    for pol in sorted(p for p in org.policies if p != "FullAWSAccess"):
        m = copy.deepcopy(org)
        for n in [m.tree, *m.tree.children]:
            if pol in n.policies:
                n.policies = [x for x in n.policies if x != pol]
        if (
            pol == "regulated-allowlist"
        ):  # removing an allow-list means the OU needs FullAWSAccess to function
            for n in m.tree.children:
                if (
                    "regulated-allowlist" not in n.policies
                    and not any(p == "FullAWSAccess" for p in n.policies)
                    and n.name == "regulated"
                ):
                    n.policies = ["FullAWSAccess", *n.policies]
        rep[pol] = sorted(
            i for i, g in ((r["id"], r["got"]) for r in run_matrix(m, cases)) if g != base[i]
        )
    return rep
