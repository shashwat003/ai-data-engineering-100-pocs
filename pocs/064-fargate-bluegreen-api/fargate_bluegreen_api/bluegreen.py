"""Blue/green controller for an ECS service behind a weighted ALB listener, driven against a simulated world.

The controller keeps its state in a plain dict so a crashed controller can resume (`tick` is idempotent per state). Invariants
checked on every tick: weights sum to 100, no weight on a target group without healthy tasks, healthy capacity never below desired.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

STEPS = (10, 50, 100)  # percent of traffic on green after each shift
BAKE_TICKS = 3  # ticks to observe each step
RPT = 400  # requests per tick


@dataclass
class World:
    desired: int = 4
    green_start_fail_rate: float = 0.0  # probability a green task fails to start/pass health
    green_error_rate: float = 0.002
    blue_error_rate: float = 0.002
    seed: int = 0
    blue_healthy: int = 4
    green_healthy: int = 0
    green_launched: int = 0
    green_failed: int = 0
    weights: tuple[int, int] = (100, 0)  # (blue, green)
    served_bad_by_green: int = 0
    log: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.rng = random.Random(self.seed)

    def launch_green(self, n: int) -> None:
        for _ in range(n):
            self.green_launched += 1
            if self.rng.random() < self.green_start_fail_rate:
                self.green_failed += 1
            else:
                self.green_healthy += 1

    def traffic(self) -> tuple[int, int, int, int]:
        """(blue_requests, blue_errors, green_requests, green_errors) for one tick."""
        gw = self.weights[1]
        g = sum(self.rng.random() < gw / 100 for _ in range(RPT))
        b = RPT - g
        be = sum(self.rng.random() < self.blue_error_rate for _ in range(b))
        ge = sum(self.rng.random() < self.green_error_rate for _ in range(g))
        self.served_bad_by_green += max(0, ge - round(g * self.blue_error_rate))
        return b, be, g, ge


class InvariantViolation(AssertionError):
    pass


def check_invariants(w: World) -> None:
    if sum(w.weights) != 100:
        raise InvariantViolation(f"weights {w.weights} do not sum to 100")
    if w.weights[0] > 0 and w.blue_healthy == 0:
        raise InvariantViolation("traffic routed to blue with no healthy tasks")
    if w.weights[1] > 0 and w.green_healthy == 0:
        raise InvariantViolation("traffic routed to green with no healthy tasks")


def new_state() -> dict:
    return {
        "phase": "launch",
        "step": -1,
        "bake": 0,
        "g_req": 0,
        "g_err": 0,
        "b_req": 0,
        "b_err": 0,
        "result": None,
        "reason": "",
    }


def tick(
    w: World,
    st: dict,
    *,
    margin: float = 0.01,
    z: float = 2.33,
    max_start_failures: int = 2,
    min_requests: int = 100,
) -> dict:
    """Advance one control-loop iteration. Mutates and returns the state."""
    if st["result"]:
        return st
    if st["phase"] == "launch":
        w.launch_green(
            w.desired - w.green_healthy
        )  # (re)launch what is missing; each attempt may fail
        if w.green_failed > max_start_failures:  # circuit breaker: too many launches failed
            return rollback(w, st, f"circuit breaker: {w.green_failed} green tasks failed to start")
        if w.green_healthy >= w.desired:
            st["phase"], st["step"] = "shift", 0
            w.weights = (100 - STEPS[0], STEPS[0])
            w.log.append(f"weights -> {w.weights}")
        check_invariants(w)
        return st
    if st["phase"] == "shift":
        b, be, g, ge = w.traffic()
        st["b_req"], st["b_err"], st["g_req"], st["g_err"] = (
            st["b_req"] + b,
            st["b_err"] + be,
            st["g_req"] + g,
            st["g_err"] + ge,
        )
        check_invariants(w)
        st["bake"] += 1
        if st["g_req"] >= min_requests:
            pg, pb = st["g_err"] / st["g_req"], st["b_err"] / max(1, st["b_req"])
            se = (pg * (1 - pg) / st["g_req"] + pb * (1 - pb) / max(1, st["b_req"])) ** 0.5
            if pg - pb - z * se > margin:  # confident the regression exceeds the tolerated margin
                return rollback(
                    w,
                    st,
                    f"green error rate {pg:.1%} vs blue {pb:.1%} at {w.weights[1]}% (lower bound {pg - pb - z * se:+.1%} > {margin:.1%})",
                )
        if st["bake"] >= BAKE_TICKS:
            st["bake"], st["g_req"], st["g_err"], st["b_req"], st["b_err"] = 0, 0, 0, 0, 0
            st["step"] += 1
            if st["step"] >= len(STEPS):
                st["phase"] = "drain"
                st["bake"] = 0
            else:
                w.weights = (100 - STEPS[st["step"]], STEPS[st["step"]])
                w.log.append(f"weights -> {w.weights}")
        return st
    if (
        st["phase"] == "drain"
    ):  # keep blue alive for the termination wait so a late problem can still roll back
        st["bake"] += 1
        w.traffic()
        check_invariants(w)
        if st["bake"] >= BAKE_TICKS:
            w.blue_healthy = 0
            st["result"], st["phase"] = "completed", "done"
            w.log.append("blue scaled to 0")
        return st
    return st


def rollback(w: World, st: dict, reason: str) -> dict:
    w.weights = (100, 0)
    w.green_healthy = 0
    st["result"], st["phase"], st["reason"] = "rolled_back", "done", reason
    w.log.append(f"rollback: {reason}")
    check_invariants(w)
    return st


def deploy(w: World, *, crash_at: int | None = None, max_ticks: int = 60, **kw) -> dict:
    """Run to completion. `crash_at` simulates the controller dying after that many ticks and resuming from persisted state."""
    st = new_state()
    for n in range(max_ticks):
        if crash_at is not None and n == crash_at:
            import json

            st = json.loads(
                json.dumps(st)
            )  # persisted and reloaded: nothing may live only in memory
        tick(w, st, **kw)
        if st["result"]:
            break
    else:
        st["result"], st["reason"] = "rolled_back", "no decision before deadline (fail closed)"
        w.weights, w.green_healthy = (100, 0), 0
    return {
        "result": st["result"],
        "reason": st["reason"],
        "ticks": n + 1,
        "bad_requests_served_by_green": w.served_bad_by_green,
        "green_launched": w.green_launched,
        "final_weights": w.weights,
        "blue_healthy": w.blue_healthy,
        "green_healthy": w.green_healthy,
    }
