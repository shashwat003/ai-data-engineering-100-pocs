"""Static analysis of IAM trust policies for GitHub Actions OIDC roles and of the permissions those roles carry."""

from __future__ import annotations

import fnmatch
import json
import re

ISSUER = "token.actions.githubusercontent.com"
SUB, AUD = f"{ISSUER}:sub", f"{ISSUER}:aud"
SAFE_SUB = re.compile(
    r"^repo:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+:(environment:[A-Za-z0-9_.-]+|ref:refs/(heads|tags)/[A-Za-z0-9_./-]+)$"
)


def _as_list(v):
    return v if isinstance(v, list) else [v]


def lint_trust_policy(doc: dict | str, account_id: str | None = None) -> list[str]:
    if isinstance(doc, str):
        doc = json.loads(doc)
    out: list[str] = []
    stmts = doc.get("Statement", [])
    if not stmts:
        return ["trust policy has no statements"]
    for i, st in enumerate(stmts):
        where = f"statement {i}"
        if st.get("Effect") != "Allow":
            continue
        principal = st.get("Principal")
        if principal in ("*", {"AWS": "*"}) or (
            isinstance(principal, dict) and "*" in _as_list(principal.get("AWS", []))
        ):
            out.append(f"{where}: Principal is * (anyone can assume the role)")
            continue
        fed = _as_list(principal.get("Federated", [])) if isinstance(principal, dict) else []
        if not fed:
            continue  # not a web-identity trust; other principals are out of scope here
        if not any(ISSUER in f for f in fed):
            out.append(f"{where}: federated principal is not the GitHub OIDC provider")
        elif account_id and not any(f"::{account_id}:oidc-provider/{ISSUER}" in f for f in fed):
            out.append(f"{where}: OIDC provider belongs to a different account")
        if "sts:AssumeRoleWithWebIdentity" not in _as_list(st.get("Action", [])):
            out.append(f"{where}: web-identity trust must use sts:AssumeRoleWithWebIdentity")
        cond = st.get("Condition", {})
        flat = {(op, k): _as_list(v) for op, kv in cond.items() for k, v in kv.items()}
        aud = [(op, v) for (op, k), v in flat.items() if k == AUD]
        sub = [(op, v) for (op, k), v in flat.items() if k == SUB]
        if not aud:
            out.append(f"{where}: no {AUD} condition (token audience unchecked)")
        elif any(v != ["sts.amazonaws.com"] for _, v in aud):
            out.append(f"{where}: audience must be exactly sts.amazonaws.com")
        if not sub:
            out.append(
                f"{where}: no {SUB} condition: ANY repository on GitHub can assume this role"
            )
        for op, vals in sub:
            for v in vals:
                if op not in ("StringEquals", "StringLike"):
                    out.append(f"{where}: unsupported operator {op} on the subject claim")
                elif "*" in v and not SAFE_SUB.match(v.replace("*", "x")):
                    out.append(
                        f"{where}: subject pattern {v!r} is too broad (wildcard beyond the ref/environment suffix)"
                    )
                elif v.endswith(":pull_request"):
                    out.append(
                        f"{where}: subject {v!r} lets pull-request workflows assume the role"
                    )
                elif "*" not in v and not SAFE_SUB.match(v):
                    out.append(f"{where}: subject {v!r} is not pinned to repo + environment/ref")
                elif v.startswith("repo:") and v.split(":")[1].endswith("/*"):
                    out.append(f"{where}: subject {v!r} trusts every repository of the owner")
    return out


def lint_permissions(doc: dict | str) -> list[str]:
    if isinstance(doc, str):
        doc = json.loads(doc)
    out = []
    for i, st in enumerate(doc.get("Statement", [])):
        if st.get("Effect") != "Allow":
            continue
        acts, res = _as_list(st.get("Action", [])), _as_list(st.get("Resource", []))
        for a in acts:
            if a == "*" or a.endswith(":*"):
                out.append(f"statement {i}: wildcard action {a}")
        if "iam:PassRole" in acts and (res == ["*"] or "*" in res):
            out.append(
                f"statement {i}: iam:PassRole on * lets the deploy role hand any role to any service (privilege escalation)"
            )
        if "iam:PassRole" in acts and "iam:PassedToService" not in json.dumps(
            st.get("Condition", {})
        ):
            out.append(f"statement {i}: iam:PassRole without an iam:PassedToService condition")
        needs_star = {
            "ecr:GetAuthorizationToken",
            "ecs:RegisterTaskDefinition",
            "ecs:DescribeTaskDefinition",
            "elasticloadbalancing:DescribeListeners",
            "elasticloadbalancing:DescribeTargetHealth",
            "elasticloadbalancing:ModifyListener",
        }
        scopable = [
            a
            for a in acts
            if a not in needs_star and a != "iam:PassRole" and not a.endswith(":*") and a != "*"
        ]
        if scopable and "*" in res:
            out.append(f"statement {i}: resource-scopable actions {scopable} granted on *")
        for a in acts:
            if (
                fnmatch.fnmatch(a, "iam:*")
                and a not in ("iam:PassRole",)
                or a in ("sts:AssumeRole",)
            ):
                out.append(f"statement {i}: deploy role should not use {a}")
    return out
