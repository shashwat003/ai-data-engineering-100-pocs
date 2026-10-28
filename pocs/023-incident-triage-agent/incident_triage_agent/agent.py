"""The triage loop. Every proposed tool call passes: schema validation -> policy (scope, risk,
severity, environment) -> optional human approval -> execution -> output redaction/filtering, and
every stage writes to the hash-chained audit log. The planner is untrusted by design: the same
checks apply whether it is the rule-based planner, an LLM, or a compromised model (POC 079).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime

from mcp_server_data_catalog.server import InvalidParams, validate_args

from .approvals import ApprovalError, ApprovalRequest, ApprovalStore, Approver, QueueApprover
from .audit import AuditLog
from .mcp_client import McpClient
from .models import ALLOWED_CATEGORIES, Finish, Incident, Observation, Step, Triage, validate_triage
from .ops import PIPELINE_OF, PIPELINES, UPSTREAM, OpsEnv, owner_of
from .planner import Planner, PlannerError, RulePlanner
from .policy import CallContext, Decision, Policy
from .redact import filter_untrusted, redact
from .tools import ToolError, ToolSpec, build_tools


@dataclass(frozen=True)
class Controls:
    """Each defence can be switched off so POC 079 can measure what it contributes."""

    arg_validation: bool = True
    policy: bool = True
    scope_binding: bool = True
    approval: bool = True
    redaction: bool = True
    injection_filter: bool = True
    step_budget: bool = True
    output_consistency: bool = True


@dataclass
class RunState:
    run_id: str
    incident: Incident
    history: list[Observation]
    steps: int = 0
    writes: int = 0
    pending: dict | None = None  # {"approval_id", "tool", "args"}
    status: str = "running"


@dataclass
class RunResult:
    run_id: str
    triage: Triage
    state: RunState


def _map_strings(value, fn: Callable[[str], str]):
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [_map_strings(v, fn) for v in value]
    if isinstance(value, dict):
        return {k: _map_strings(v, fn) for k, v in value.items()}
    return value


class TriageAgent:
    def __init__(
        self,
        ops: OpsEnv,
        mcp: McpClient,
        *,
        planner: Planner | None = None,
        policy: Policy | None = None,
        approver: Approver | None = None,
        controls: Controls | None = None,
        state_db: sqlite3.Connection | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self.ops, self.planner = ops, planner or RulePlanner()
        self.policy = policy or Policy.load()
        self.approver = approver or QueueApprover()
        self.controls = controls or Controls()
        self.tools: dict[str, ToolSpec] = build_tools(ops, mcp)
        self.con = state_db or sqlite3.connect(":memory:", check_same_thread=False)
        iso = (lambda: now().isoformat(timespec="seconds")) if now else None
        self.audit = AuditLog(self.con, iso)
        self.approvals = ApprovalStore(
            self.con,
            allowed_approvers=self.policy.approvers,
            ttl_minutes=self.policy.ttl_minutes,
            now=now,
        )
        self.con.execute("CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, state TEXT)")
        self._seq = 0

    # ------------------------------------------------------------------ public API

    def run(self, incident: Incident) -> RunResult:
        self._seq += 1
        run_id = f"{incident.id}-r{self._seq}"
        desc = incident.description
        if self.controls.injection_filter:
            desc, flagged = filter_untrusted(desc)
            if flagged:
                self.audit.append(run_id, "injection_filtered", {"source": "incident.description"})
        state = RunState(
            run_id, replace(incident, description=desc), []
        )  # planner sees the filtered text
        self.audit.append(run_id, "run_started", {
            "incident": {**asdict(incident), "description": desc[:300]},
            "controls": asdict(self.controls)})  # fmt: skip
        return self._loop(state)

    def resume(self, run_id: str) -> RunResult:
        """Continue a run that was waiting for a human decision."""
        state = self._load(run_id)
        if state.pending is None:
            raise ApprovalError(f"{run_id} is not waiting for approval")
        rec = self.approvals.get(state.pending["approval_id"])
        if rec["status"] == "pending":
            return self._finish(state, None)  # still waiting
        step = Step(state.pending["tool"], state.pending["args"])
        state.pending = None
        if rec["status"] == "approved":
            obs = self._execute_approved(state, step, rec["id"])
        else:
            obs = Observation(step.tool, step.args, "approval_denied", detail=rec["status"])
        state.history = [o for o in state.history if o.outcome != "pending_approval"]
        state.history.append(obs)  # the resolved outcome replaces the pending placeholder
        state.status = "running"
        return self._loop(state)

    # ------------------------------------------------------------------ loop

    def _loop(self, state: RunState) -> RunResult:
        inc, c = state.incident, self.controls
        finish: Finish | None = None
        while True:
            if c.step_budget and state.steps >= self.policy.limits["max_steps"]:
                state.status = "budget_exhausted"
                self.audit.append(state.run_id, "budget_exhausted", {"steps": state.steps})
                break
            state.steps += 1
            try:
                nxt = self.planner.next(inc, state.history)
            except PlannerError as exc:
                state.status = "failed"
                self.audit.append(state.run_id, "planner_error", {"error": str(exc)[:200]})
                break
            if isinstance(nxt, Finish):
                finish = nxt
                break
            self.audit.append(state.run_id, "tool_proposed", {"tool": nxt.tool, "args": nxt.args,
                                                              "rationale": nxt.rationale[:200]})  # fmt: skip
            obs = self._execute(state, nxt)
            state.history.append(obs)
            if obs.outcome == "pending_approval":
                state.status = "awaiting_approval"
                return self._finish(state, None)
        return self._finish(state, finish)

    def _execute(self, state: RunState, step: Step) -> Observation:
        c, rid = self.controls, state.run_id
        tool = self.tools.get(step.tool)
        if tool is None:
            self.audit.append(rid, "tool_rejected", {"tool": step.tool, "why": "unknown tool"})
            return Observation(step.tool, step.args, "rejected", detail="unknown tool")
        args = step.args
        if c.arg_validation:
            try:
                args = validate_args(tool.schema, step.args)
            except InvalidParams as exc:
                self.audit.append(rid, "tool_rejected", {"tool": step.tool, "why": str(exc)[:200]})
                return Observation(step.tool, step.args, "rejected", detail=str(exc)[:200])
        decision = self._decide(state, tool, args)
        self.audit.append(rid, "policy_decision", {"tool": step.tool, "args": args,
                                                   **asdict(decision)})  # fmt: skip
        if decision.effect == "deny":
            return Observation(step.tool, args, "denied", detail=decision.reason)
        if decision.effect == "approve" and c.approval:
            req = self.approvals.request(rid, step.tool, args, decision.reason)
            self.audit.append(rid, "approval_requested", {"approval_id": req.id, "tool": step.tool,
                                                          "args": args})  # fmt: skip
            verdict = self.approver.decide(
                ApprovalRequest(req.id, rid, step.tool, args, decision.reason)
            )
            if verdict is None:
                state.pending = {"approval_id": req.id, "tool": step.tool, "args": args}
                return Observation(step.tool, args, "pending_approval", detail=f"approval {req.id}")
            ok, who, why = verdict
            try:
                self.approvals.decide(req.id, who, ok, why)
            except ApprovalError as exc:
                self.audit.append(
                    rid, "approval_rejected", {"approval_id": req.id, "error": str(exc)}
                )
                return Observation(step.tool, args, "approval_denied", detail=str(exc))
            self.audit.append(rid, "approval_decided", {"approval_id": req.id, "approver": who,
                                                        "approved": ok, "reason": why[:200]})  # fmt: skip
            if not ok:
                return Observation(step.tool, args, "approval_denied", detail="denied by human")
            return self._execute_approved(state, Step(step.tool, args), req.id)
        return self._run_tool(state, tool, args)

    def _execute_approved(self, state: RunState, step: Step, approval_id: int) -> Observation:
        try:
            self.approvals.consume(approval_id, step.tool, step.args)
        except ApprovalError as exc:
            self.audit.append(state.run_id, "approval_rejected", {"approval_id": approval_id,
                                                                  "error": str(exc)})  # fmt: skip
            return Observation(step.tool, step.args, "approval_denied", detail=str(exc))
        return self._run_tool(state, self.tools[step.tool], step.args)

    def _run_tool(self, state: RunState, tool: ToolSpec, args: dict) -> Observation:
        rid = state.run_id
        try:
            out = tool.run(**args)
        except ToolError as exc:
            self.audit.append(rid, "tool_error", {"tool": tool.name, "error": str(exc)[:200]})
            return Observation(tool.name, args, "error", detail=str(exc)[:200])
        except Exception as exc:  # a handler bug must not take the loop down or leak internals
            self.audit.append(rid, "tool_error", {"tool": tool.name, "error": type(exc).__name__})
            return Observation(tool.name, args, "error", detail="tool failed")
        c = self.controls
        if c.redaction:
            out = _map_strings(out, redact)
        if c.injection_filter and tool.untrusted:
            hits = []
            out = _map_strings(
                out, lambda s: (lambda r: (hits.append(r[1]), r[0])[1])(filter_untrusted(s))
            )
            if any(hits):
                self.audit.append(rid, "injection_filtered", {"source": tool.name,
                                                              "strings": sum(hits)})  # fmt: skip
        if self.policy.risk(tool.name) in ("write", "forbidden"):
            state.writes += 1
        self.audit.append(rid, "tool_executed", {"tool": tool.name, "args": args,
                                                 "result": json.dumps(out, default=str)[:400]})  # fmt: skip
        return Observation(tool.name, args, "ok", data=out)

    # ------------------------------------------------------------------ policy context

    def _decide(self, state: RunState, tool: ToolSpec, args: dict) -> Decision:
        if not self.controls.policy:
            return Decision("allow", "policy-off", "policy engine disabled")
        inc = state.incident
        risk = self.policy.risk(tool.name)
        if (self.controls.step_budget and risk == "write"
                and state.writes >= self.policy.limits["max_write_actions"]):  # fmt: skip
            return Decision(
                "deny", "write-budget", "write-action budget for this incident is used up"
            )
        pipe = args.get("pipeline") or PIPELINE_OF.get(args.get("dataset", ""))
        env = PIPELINES[pipe][1] if pipe in PIPELINES else None
        datasets = [inc.dataset, *UPSTREAM.get(inc.dataset, [])]
        teams = {t for d in datasets if (t := owner_of(d))}
        if inc.alert_type == "pii_exposure_suspected":
            teams.add("security")
        scope = {"pipeline": {inc.pipeline or PIPELINE_OF.get(inc.dataset, "")},
                 "dataset": {inc.dataset}, "team": teams}  # fmt: skip
        ctx = CallContext(inc.severity, env, scope)
        return self.policy.evaluate(tool.name, args, ctx, scope_binding=self.controls.scope_binding)

    # ------------------------------------------------------------------ output

    def _finish(self, state: RunState, finish: Finish | None) -> RunResult:
        inc, h = state.incident, state.history
        evidence = [{"tool": o.tool, "finding": self._summarise(o)} for o in h
                    if o.outcome == "ok" and self.policy.risk(o.tool) == "read"]  # fmt: skip
        executed = [{"tool": o.tool, "args": o.args, "result": o.data} for o in h
                    if o.outcome == "ok" and self.policy.risk(o.tool) != "read"]  # fmt: skip
        pending = [{"tool": o.tool, "args": o.args, "approval": o.detail} for o in h
                   if o.outcome == "pending_approval"]  # fmt: skip
        blocked = [{"tool": o.tool, "args": o.args, "outcome": o.outcome, "detail": o.detail}
                   for o in h if o.outcome in ("denied", "rejected", "approval_denied")]  # fmt: skip
        if state.status == "running":
            clean = (
                finish is not None and not blocked and not pending and finish.category != "unknown"
            )
            state.status = "resolved" if clean else "escalated"
        if (finish is not None and self.controls.output_consistency
                and finish.category not in ALLOWED_CATEGORIES.get(inc.alert_type, set())):  # fmt: skip
            self.audit.append(state.run_id, "output_inconsistent", {"category": finish.category,
                                                                    "alert_type": inc.alert_type})  # fmt: skip
            finish = Finish("unknown", "Planner conclusion rejected: inconsistent with the alert type.",
                            0.0, ("Manual triage required.",))  # fmt: skip
            state.status = "escalated"
        triage = Triage(
            incident_id=inc.id, status=state.status,
            category=finish.category if finish else "unknown", severity=inc.severity,
            root_cause=finish.root_cause if finish else "Triage incomplete; see audit log.",
            confidence=finish.confidence if finish else 0.0, evidence=evidence,
            actions_executed=executed, actions_pending_approval=pending, actions_blocked=blocked,
            next_steps=list(finish.next_steps) if finish else ["Manual triage required."],
        )  # fmt: skip
        out = triage.to_dict()
        if self.controls.redaction:
            out = _map_strings(out, redact)
        problems = validate_triage(out)
        if problems:
            self.audit.append(state.run_id, "output_invalid", {"fields": problems})
            out = Triage(inc.id, "failed", "unknown", inc.severity, "Output failed validation.", 0.0,
                         next_steps=["Manual triage required."]).to_dict()  # fmt: skip
        self.audit.append(state.run_id, "run_finished", {"status": out["status"],
                                                         "category": out["category"],
                                                         "steps": state.steps})  # fmt: skip
        self._save(state)
        return RunResult(state.run_id, Triage(**out), state)

    @staticmethod
    def _summarise(o: Observation) -> str:
        d = o.data if isinstance(o.data, dict) else {}
        if o.tool == "get_pipeline_runs":
            runs = d.get("runs", [])
            first = runs[0] if runs else {}
            return (f"{o.args.get('pipeline')}: {len(runs)} recent runs, latest {first.get('status')}"
                    f" ({first.get('error_class')}), rows {first.get('rows_loaded')}")  # fmt: skip
        if o.tool == "get_dataset_schema":
            return f"owner={d.get('owner')} freshness_hours={d.get('freshness_hours')} rows={d.get('row_count')}"
        if o.tool == "get_lineage":
            return f"upstream={d.get('upstream')} downstream={d.get('downstream')}"
        if o.tool == "search_datasets":
            return f"{len(d.get('items', []))} catalogue matches"
        return json.dumps(d, default=str)[:120]

    # ------------------------------------------------------------------ persistence

    def _save(self, state: RunState) -> None:
        blob = {"incident": asdict(state.incident), "history": [asdict(o) for o in state.history],
                "steps": state.steps, "writes": state.writes, "pending": state.pending,
                "status": state.status}  # fmt: skip
        self.con.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?)",
            (state.run_id, json.dumps(blob, default=str)),
        )
        self.con.commit()

    def _load(self, run_id: str) -> RunState:
        row = self.con.execute("SELECT state FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ApprovalError(f"unknown run {run_id}")
        b = json.loads(row[0])
        return RunState(run_id, Incident(**b["incident"]), [Observation(**o) for o in b["history"]],
                        b["steps"], b["writes"], b["pending"], b["status"])  # fmt: skip
