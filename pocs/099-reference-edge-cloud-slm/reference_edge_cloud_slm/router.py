"""Edge-first router with cloud escalation, a circuit breaker and graceful degradation.

The edge model answers when its confidence is high enough; otherwise the request goes to the cloud model. If the cloud
is unavailable (exception, timeout) the edge answer is returned and flagged as degraded; after repeated failures a
circuit breaker stops calling the cloud for a cool-down. Latency and cost of the cloud leg are MODELLED constants.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np


class CloudUnavailable(RuntimeError):
    """Raised by a cloud callable that timed out or failed."""


@dataclass(frozen=True)
class Costs:
    edge_ms: float = 1.0  # replaced by the measured ONNX median in the demo
    cloud_rtt_ms: float = 120.0  # ASSUMED network round trip + service time
    cloud_timeout_ms: float = 400.0  # time wasted on a failed cloud call
    cloud_usd: float = 0.0004  # ASSUMED price per cloud inference


@dataclass
class CircuitBreaker:
    failures_to_open: int = 3
    cooldown_s: float = 30.0
    clock: Callable[[], float] = time.monotonic
    _fails: int = 0
    _opened_at: float | None = None

    def allow(self) -> bool:
        if self._opened_at is None:
            return True
        if self.clock() - self._opened_at >= self.cooldown_s:
            return True  # half-open: let one probe through
        return False

    @property
    def open(self) -> bool:
        return not self.allow()

    def record(self, ok: bool) -> None:
        if ok:
            self._fails, self._opened_at = 0, None
            return
        self._fails += 1
        if self._fails >= self.failures_to_open:
            self._opened_at = self.clock()


@dataclass(frozen=True)
class Decision:
    label: int
    source: str  # edge | cloud | edge-degraded
    confidence: float
    latency_ms: float
    cost_usd: float


@dataclass
class HybridRouter:
    edge_proba: Callable[[np.ndarray], np.ndarray]  # (n, d) -> P(y=1)
    cloud_predict: Callable[[np.ndarray], int]
    threshold: float = 0.8
    costs: Costs = field(default_factory=Costs)
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    force_cloud: bool = False  # set by the drift controller in emergencies

    def predict(self, x: np.ndarray) -> Decision:
        p = float(
            self.edge_proba(x.reshape(1, -1))[0]
        )  # InputError from the edge engine is NOT swallowed
        conf = max(p, 1 - p)
        edge_label = int(p > 0.5)
        if conf >= self.threshold and not self.force_cloud:
            return Decision(edge_label, "edge", conf, self.costs.edge_ms, 0.0)
        if not self.breaker.allow():
            return Decision(edge_label, "edge-degraded", conf, self.costs.edge_ms, 0.0)
        try:
            label = int(self.cloud_predict(x))
        except CloudUnavailable:
            self.breaker.record(False)
            return Decision(
                edge_label,
                "edge-degraded",
                conf,
                self.costs.edge_ms + self.costs.cloud_timeout_ms,
                0.0,
            )
        self.breaker.record(True)
        return Decision(
            label, "cloud", conf, self.costs.edge_ms + self.costs.cloud_rtt_ms, self.costs.cloud_usd
        )
