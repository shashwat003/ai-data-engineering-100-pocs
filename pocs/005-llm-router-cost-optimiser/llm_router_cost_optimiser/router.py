"""Routing policies. Each decides which model(s) answer a task and records cost in a ledger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pocs_common import CostLedger

from .models import Model, ModelOutput
from .tasks import Task, heuristic_difficulty, is_correct


@dataclass(frozen=True)
class RouteResult:
    answer: str
    models_used: tuple[str, ...]
    cost_usd: float
    latency_ms: float
    escalated: bool
    degraded: bool = False  # wanted to escalate but the budget was exhausted


class Router(Protocol):
    name: str

    def route(self, task: Task, ledger: CostLedger) -> RouteResult: ...


def _call(model: Model, task: Task, ledger: CostLedger) -> tuple[ModelOutput, float]:
    out = model.generate(task)
    return out, ledger.record(model.name, out.input_tokens, out.output_tokens)


class SingleModelRouter:
    def __init__(self, model: Model, name: str):
        self.model, self.name = model, name

    def route(self, task: Task, ledger: CostLedger) -> RouteResult:
        out, cost = _call(self.model, task, ledger)
        return RouteResult(out.answer, (self.model.name,), cost, out.latency_ms, False)


class CascadeRouter:
    """Ask the small model first; escalate to the large model when its confidence is below threshold."""

    def __init__(self, small: Model, large: Model, threshold: float, name: str | None = None):
        self.small, self.large, self.threshold = small, large, threshold
        self.name = name or f"cascade@{threshold:.2f}"

    def route(self, task: Task, ledger: CostLedger) -> RouteResult:
        s_out, s_cost = _call(self.small, task, ledger)
        if s_out.confidence >= self.threshold:
            return RouteResult(s_out.answer, (self.small.name,), s_cost, s_out.latency_ms, False)
        if ledger.exhausted():  # budget guard: degrade to the cheap answer instead of overspending
            return RouteResult(
                s_out.answer, (self.small.name,), s_cost, s_out.latency_ms, False, degraded=True
            )
        l_out, l_cost = _call(self.large, task, ledger)
        return RouteResult(
            l_out.answer,
            (self.small.name, self.large.name),
            s_cost + l_cost,
            s_out.latency_ms + l_out.latency_ms,  # sequential: the user waits for both
            True,
        )


class FeatureRouter:
    """Route on prompt features only: predicted-hard tasks go straight to the large model.

    With cascade_threshold set, predicted-easy tasks use the cascade instead of the small model alone.
    """

    def __init__(
        self,
        small: Model,
        large: Model,
        cutoff: float,
        cascade_threshold: float | None = None,
        name: str | None = None,
    ):
        self.small, self.large, self.cutoff = small, large, cutoff
        self._cascade = (
            CascadeRouter(small, large, cascade_threshold)
            if cascade_threshold is not None
            else None
        )
        self.name = name or "feature_router"

    def route(self, task: Task, ledger: CostLedger) -> RouteResult:
        if heuristic_difficulty(task.prompt) >= self.cutoff:
            out, cost = _call(self.large, task, ledger)
            return RouteResult(out.answer, (self.large.name,), cost, out.latency_ms, True)
        if self._cascade is not None:
            return self._cascade.route(task, ledger)
        out, cost = _call(self.small, task, ledger)
        return RouteResult(out.answer, (self.small.name,), cost, out.latency_ms, False)


class OracleRouter:
    """Reference only (not deployable): uses the ground truth to pick the cheapest correct model."""

    def __init__(self, small: Model, large: Model, name: str = "oracle"):
        self.small, self.large, self.name = small, large, name

    def route(self, task: Task, ledger: CostLedger) -> RouteResult:
        peek = self.small.generate(task)
        if is_correct(task, peek.answer):
            out, cost = _call(self.small, task, ledger)
            return RouteResult(out.answer, (self.small.name,), cost, out.latency_ms, False)
        out, cost = _call(self.large, task, ledger)
        return RouteResult(out.answer, (self.large.name,), cost, out.latency_ms, True)
