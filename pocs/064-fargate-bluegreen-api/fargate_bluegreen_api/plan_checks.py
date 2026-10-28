"""Policy checks over `terraform show -json` of the infra plan (no cloud access needed)."""

from __future__ import annotations

import json
import re

from .oidc import lint_permissions, lint_trust_policy

DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def _after(plan: dict) -> list[tuple[str, dict]]:
    return [
        (r["type"], r["change"]["after"] or {})
        for r in plan["resource_changes"]
        if "delete" not in r["change"]["actions"]
    ]


def check_plan(plan: dict) -> list[str]:
    out: list[str] = []
    res = _after(plan)
    of = lambda t: [a for ty, a in res if ty == t]  # noqa: E731
    for td in of("aws_ecs_task_definition"):
        for c in json.loads(td["container_definitions"]):
            n = c.get("name", "?")
            if not DIGEST.search(c.get("image", "")):
                out.append(f"task container {n}: image not pinned by digest")
            if not c.get("readonlyRootFilesystem"):
                out.append(f"task container {n}: root filesystem is writable")
            if c.get("privileged"):
                out.append(f"task container {n}: privileged")
            if "logConfiguration" not in c:
                out.append(
                    f"task container {n}: no log configuration (failed deployments would be undiagnosable)"
                )
            if c.get("user") in ("root", "0"):
                out.append(f"task container {n}: runs as root")
        if not td.get("execution_role_arn"):
            out.append("task definition: no execution role")
    for svc in of("aws_ecs_service"):
        cb = (svc.get("deployment_circuit_breaker") or [{}])[0]
        if not (cb.get("enable") and cb.get("rollback")):
            out.append("ecs service: deployment circuit breaker with rollback is not enabled")
        if svc.get("desired_count", 0) < 2:
            out.append("ecs service: desired_count < 2 (no capacity during replacement)")
        if svc.get("deployment_minimum_healthy_percent", 100) < 100:
            out.append("ecs service: minimum healthy percent < 100 (capacity drops during deploys)")
        nc = (svc.get("network_configuration") or [{}])[0]
        if nc.get("assign_public_ip"):
            out.append("ecs service: tasks get public IPs")
        if len(nc.get("subnets") or []) < 2 and nc.get("subnets") is not None:
            out.append("ecs service: tasks in a single subnet/AZ")
    for lb in of("aws_lb"):
        if lb.get("internal") is False:
            out.append("load balancer is internet-facing")
        if not lb.get("drop_invalid_header_fields"):
            out.append("load balancer: drop_invalid_header_fields disabled")
    listeners = of("aws_lb_listener")
    for ls in listeners:
        if (
            ls.get("protocol") == "HTTP"
            and (ls.get("default_action") or [{}])[0].get("type") != "redirect"
        ):
            out.append("http listener serves traffic instead of redirecting to HTTPS")
        if (
            ls.get("protocol") == "HTTPS"
            and "TLS13" not in (ls.get("ssl_policy") or "")
            and "TLS-1-2" not in (ls.get("ssl_policy") or "")
        ):
            out.append("https listener: weak or default TLS policy")
        for act in ls.get("default_action") or []:
            weights = [
                tg["weight"] for f in act.get("forward") or [] for tg in f.get("target_group") or []
            ]
            if weights and sum(weights) != 100:
                out.append(f"listener weights sum to {sum(weights)}, not 100")
            if weights and max(weights) != 100:
                out.append("steady-state listener is not 100/0 between blue and green")
    for tg in of("aws_lb_target_group"):
        if (tg.get("deregistration_delay") not in (None, "")) and int(
            tg["deregistration_delay"]
        ) > 120:
            out.append("target group: long deregistration delay slows rollback")
    for role in of("aws_iam_role"):
        pol = role.get("assume_role_policy")
        if pol and "token.actions.githubusercontent.com" in pol:
            out += [f"deploy role trust: {m}" for m in lint_trust_policy(pol)]
    for rp in of("aws_iam_role_policy"):
        if "policy" not in rp:
            out.append("deploy role permissions are unknown at plan time and could not be linted")
            continue
        out += [f"deploy role permissions: {m}" for m in lint_permissions(rp["policy"])]
    return out
