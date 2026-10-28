"""Autoscaling policies. Each returns a desired instance count from the current state."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def fixed(n: int):
    return lambda t, s: n


@dataclass
class TargetTracking:
    """Keep requests per second per instance near `target` (the shape of SageMaker's invocations-per-instance tracking).

    Scale-out is allowed every `out_cooldown` seconds, scale-in every `in_cooldown`; scale-in uses a conservative ratio so the
    fleet does not oscillate.
    """

    target_rps_per_instance: float
    out_cooldown: float = 60.0
    in_cooldown: float = 300.0
    min_instances: int = 1
    max_instances: int = 40
    _last_out: float = field(default=-1e9, init=False)
    _last_in: float = field(default=-1e9, init=False)
    _hist: list = field(default_factory=list, init=False)

    def __call__(self, t: float, s: dict) -> int:
        self._hist.append((t, s["rate"]))
        self._hist = [(a, b) for a, b in self._hist if t - a <= self.in_cooldown]
        want = math.ceil(s["rate"] / self.target_rps_per_instance)
        cur = s["instances"] + s["starting"]
        if want > cur and t - self._last_out >= self.out_cooldown:
            self._last_out = t
            return min(self.max_instances, max(self.min_instances, want))
        peak = max(r for _, r in self._hist)
        want_in = math.ceil(
            peak / self.target_rps_per_instance
        )  # scale in only to what the recent PEAK needs
        if (
            want_in < s["instances"]
            and t - self._last_in >= self.in_cooldown
            and t - self._last_out >= self.in_cooldown
        ):
            self._last_in = t
            return max(self.min_instances, want_in)
        return cur


@dataclass
class StepScaling:
    """Utilisation thresholds with fixed steps (+/-)."""

    high: float = 0.7
    low: float = 0.3
    step_out: int = 2
    step_in: int = 1
    cooldown: float = 120.0
    min_instances: int = 1
    max_instances: int = 40
    _last: float = field(default=-1e9, init=False)

    def __call__(self, t: float, s: dict) -> int:
        cur = s["instances"] + s["starting"]
        if t - self._last < self.cooldown:
            return cur
        if s["util"] > self.high:
            self._last = t
            return min(self.max_instances, cur + self.step_out)
        if s["util"] < self.low and cur > self.min_instances:
            self._last = t
            return max(self.min_instances, cur - self.step_in)
        return cur


@dataclass
class Scheduled:
    """A time-of-day floor on top of another policy: capacity is pre-provisioned before known peaks (startup delay hidden)."""

    floors: list[
        tuple[float, float, int]
    ]  # (start_s, end_s, min instances), seconds since midnight
    inner: object

    def __call__(self, t: float, s: dict) -> int:
        tod = t % 86400
        floor = max((n for a, b, n in self.floors if a <= tod < b), default=0)
        return max(floor, int(self.inner(t, s)))
