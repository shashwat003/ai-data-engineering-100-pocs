"""Declarative policy engine: allow / approve / deny per tool call, with incident scope binding."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_POLICY = Path(__file__).resolve().parent / "policy.yaml"


@dataclass(frozen=True)
class Decision:
    effect: str  # allow | approve | deny
    rule: str
    reason: str


@dataclass(frozen=True)
class CallContext:
    severity: int
    pipeline_env: str | None  # environment of the pipeline the call targets, if any
    scope: dict[str, set[str]]  # argument name -> values the incident entitles the agent to touch


class Policy:
    def __init__(self, spec: dict):
        self.spec = spec
        self.tools: dict = spec["tools"]
        self.limits: dict = spec["limits"]
        self.approvers = set(spec["approvers"])
        self.ttl_minutes = int(spec.get("approval_ttl_minutes", 60))

    @classmethod
    def load(cls, path: str | Path = DEFAULT_POLICY) -> Policy:
        return cls(yaml.safe_load(Path(path).read_text()))

    def risk(self, tool: str) -> str:
        return self.tools.get(tool, {}).get("risk", "unknown")

    def evaluate(
        self, tool: str, args: dict, ctx: CallContext, *, scope_binding: bool = True
    ) -> Decision:
        spec = self.tools.get(tool)
        if spec is None:
            return Decision("deny", "default-deny", f"{tool!r} is not in the policy")
        if spec.get("decision") == "deny":
            return Decision("deny", f"{tool}:forbidden", "forbidden tool")
        if scope_binding:
            for arg in spec.get("scope", []):
                if args.get(arg) not in ctx.scope.get(arg, set()):
                    return Decision("deny", f"{tool}:scope", f"{arg}={args.get(arg)!r} is outside this incident")  # fmt: skip
        if "decision" in spec:
            return Decision(spec["decision"], f"{tool}:static", "tool-level rule")
        for i, rule in enumerate(spec.get("rules", [])):
            when = rule.get("when", {})
            if "pipeline_env" in when and ctx.pipeline_env != when["pipeline_env"]:
                continue
            if "severity_lte" in when and ctx.severity > when["severity_lte"]:
                continue
            return Decision(rule["decision"], f"{tool}:rule{i}", f"matched {when or 'default'}")
        return Decision("deny", f"{tool}:no-rule", "no rule matched")
