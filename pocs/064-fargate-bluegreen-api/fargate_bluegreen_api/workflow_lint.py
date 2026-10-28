"""Lint a GitHub Actions deploy workflow for credential and injection hygiene."""

from __future__ import annotations

import re

import yaml

SHA = re.compile(r"^[0-9a-f]{40}$")
UNTRUSTED = re.compile(
    r"\$\{\{\s*github\.(event\.(issue|pull_request|comment|review|head_commit|commits)|head_ref)[^}]*\}\}"
)
LONG_LIVED = re.compile(r"AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN)|secrets\.AWS_", re.I)


def _on(doc: dict):
    return doc.get("on", doc.get(True, {}))  # YAML 1.1 parses the key `on` as boolean True


def lint_workflow(text: str) -> list[str]:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return [f"invalid YAML: {e}"]
    if not isinstance(doc, dict) or "jobs" not in doc:
        return ["not a workflow (no jobs)"]
    out: list[str] = []
    triggers = _on(doc)
    trig = (
        set(triggers)
        if isinstance(triggers, dict)
        else set(triggers if isinstance(triggers, list) else [triggers])
    )
    top_perm = doc.get("permissions")
    if top_perm is None:
        out.append("no top-level permissions: the default token may be read-write")
    elif top_perm == "write-all" or (isinstance(top_perm, dict) and "write" in top_perm.values()):
        out.append("top-level permissions grant write access to every job")
    if LONG_LIVED.search(text):
        out.append(
            "long-lived AWS credentials (access keys / secrets.AWS_*) are referenced: use OIDC role assumption"
        )
    for name, job in doc["jobs"].items():
        perms = job.get("permissions", top_perm) or {}
        has_oidc = isinstance(perms, dict) and perms.get("id-token") == "write"
        assumes = any(
            "configure-aws-credentials" in str(s.get("uses", "")) for s in job.get("steps", [])
        )
        if assumes and not has_oidc:
            out.append(f"job {name}: assumes an AWS role but lacks `id-token: write`")
        if has_oidc and not assumes:
            out.append(
                f"job {name}: has `id-token: write` but never assumes a role (unneeded permission)"
            )
        if has_oidc and ({"pull_request_target", "workflow_run", "issue_comment"} & trig):
            out.append(
                f"job {name}: OIDC token requested from a workflow triggered by {sorted({'pull_request_target', 'workflow_run', 'issue_comment'} & trig)} (untrusted context)"
            )
        if has_oidc and "environment" not in job:
            out.append(
                f"job {name}: OIDC deploy without a protected `environment` (no approval gate, and the subject claim cannot pin it)"
            )
        if has_oidc and "concurrency" not in job and "concurrency" not in doc:
            out.append(f"job {name}: no concurrency group: two deployments could race")
        if "timeout-minutes" not in job:
            out.append(f"job {name}: no timeout-minutes")
        for i, step in enumerate(job.get("steps", [])):
            uses = step.get("uses")
            if uses and not uses.startswith("./"):
                ref = uses.split("@")[-1] if "@" in uses else ""
                if not SHA.match(ref):
                    out.append(
                        f"job {name} step {i}: {uses} is not pinned to a commit SHA (tags can be moved)"
                    )
            if "configure-aws-credentials" in str(uses):
                w = step.get("with", {})
                if "role-to-assume" not in w:
                    out.append(
                        f"job {name} step {i}: configure-aws-credentials without role-to-assume"
                    )
                if any(k in w for k in ("aws-access-key-id", "aws-secret-access-key")):
                    out.append(
                        f"job {name} step {i}: static keys passed to configure-aws-credentials"
                    )
            run = step.get("run", "")
            if UNTRUSTED.search(run):
                out.append(
                    f"job {name} step {i}: untrusted event data interpolated into a shell script (script injection)"
                )
    return out
