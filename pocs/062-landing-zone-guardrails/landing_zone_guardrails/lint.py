"""Static checks on SCP documents and the org tree, before anything is applied."""

from __future__ import annotations

import json

from .simulator import NEGATED, POSITIVE, Organisation

LIMIT_CHARS = 5120
MAX_POLICIES_PER_TARGET = 5
SUPPORTED_OPS = POSITIVE | NEGATED | {"Null", "Bool"}


def lint_policy(name: str, doc: dict, break_glass: str) -> list[str]:
    out = []
    if len(json.dumps(doc, separators=(",", ":"))) > LIMIT_CHARS:
        out.append(f"{name}: exceeds the {LIMIT_CHARS}-character SCP limit")
    if doc.get("Version") != "2012-10-17":
        out.append(f"{name}: Version must be 2012-10-17")
    sids = [s.get("Sid") for s in doc.get("Statement", [])]
    if len(set(sids)) != len(sids) or None in sids:
        out.append(f"{name}: every statement needs a unique Sid")
    for st in doc.get("Statement", []):
        sid = st.get("Sid", "?")
        if st.get("Effect") not in ("Allow", "Deny"):
            out.append(f"{name}/{sid}: bad Effect")
        if "Principal" in st or "NotPrincipal" in st:
            out.append(f"{name}/{sid}: SCPs cannot have a Principal")
        if st.get("Effect") == "Allow" and "NotAction" in st:
            out.append(
                f"{name}/{sid}: Allow with NotAction grants nearly everything; use an explicit allow-list"
            )
        for op in st.get("Condition", {}):
            if op not in SUPPORTED_OPS:
                out.append(
                    f"{name}/{sid}: condition operator {op} is not supported by the simulator"
                )
        if st.get("Effect") == "Deny":
            text = json.dumps(st)
            exempt = break_glass in text
            root_rule = "PrincipalArn" in text and ":root" in text
            leave = "LeaveOrganization" in text
            if not exempt and not root_rule and not leave and "Condition" not in st:
                out.append(
                    f"{name}/{sid}: unconditional Deny without a break-glass exemption can lock operators out"
                )
            if "Action" in st and st["Action"] in ("*", ["*"]) and not root_rule and not exempt:
                out.append(f"{name}/{sid}: Deny * without exemption")
    return out


def lint_org(org: Organisation, break_glass: str) -> list[str]:
    out: list[str] = []
    for name, doc in org.policies.items():
        if name != "FullAWSAccess":
            out += lint_policy(name, doc, break_glass)
    for n in [org.tree, *org.tree.children]:
        if len(n.policies) > MAX_POLICIES_PER_TARGET:
            out.append(f"{n.name}: more than {MAX_POLICIES_PER_TARGET} SCPs attached")
        for p in n.policies:
            if p not in org.policies:
                out.append(f"{n.name}: attached policy {p!r} does not exist")
    if "FullAWSAccess" not in org.tree.policies:
        out.append(
            "root: FullAWSAccess detached from the root; every account would be denied everything unless an allow-list exists"
        )
    return out
