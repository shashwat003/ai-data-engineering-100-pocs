"""Trajectory model, task specification and metrics for evaluating tool-using agents.

A trajectory is the ordered list of tool calls an agent made and what happened to each. The
metrics judge *how* the task was done (right tools, right order, nothing forbidden, no waste), not
just the final answer, and are deterministic so they can gate a CI pipeline.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

OK, BLOCKED = "ok", ("denied", "rejected", "approval_denied")


@dataclass(frozen=True)
class Call:
    tool: str
    args: dict = field(default_factory=dict)
    outcome: str = OK  # ok | denied | rejected | approval_denied | error | pending_approval


@dataclass(frozen=True)
class Spec:
    required: tuple[str, ...]  # tools that must succeed, in this relative order
    required_any: frozenset[str] = frozenset()  # must succeed, any order (evidence gathering)
    allowed: frozenset[str] = frozenset()  # other tools that are fine (reads, notifications)
    forbidden: frozenset[str] = frozenset()  # must never be executed (attempts are also reported)
    must_precede: tuple[tuple[str, str], ...] = ()  # (a, b): a must succeed before b is called
    arg_checks: dict[str, dict] = field(default_factory=dict)  # tool -> args subset that must match
    max_steps: int = 12
    max_repeats: int = 0  # identical (tool, args) calls beyond the first


def lcs_len(a: Sequence[str], b: Sequence[str]) -> int:
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i, x in enumerate(a, 1):
        for j, y in enumerate(b, 1):
            dp[i][j] = dp[i - 1][j - 1] + 1 if x == y else max(dp[i - 1][j], dp[i][j - 1])
    return dp[-1][-1]


def evaluate(traj: Sequence[Call], spec: Spec) -> dict:
    ok_tools = [c.tool for c in traj if c.outcome == OK]
    expected = set(spec.required) | spec.required_any | spec.allowed
    n = len(traj)
    seen: set[tuple[str, str]] = set()
    repeats = 0
    for c in traj:
        key = (c.tool, repr(sorted(c.args.items())))
        repeats += key in seen
        seen.add(key)
    need = len(spec.required) + len(spec.required_any)
    got = lcs_len(spec.required, ok_tools) + len(spec.required_any & set(ok_tools))
    req_recall = got / need if need else 1.0
    forbidden_attempts = sum(c.tool in spec.forbidden for c in traj)
    forbidden_executed = sum(c.tool in spec.forbidden and c.outcome == OK for c in traj)
    off_plan = sum(c.outcome == OK and c.tool not in expected for c in traj)
    precedence_violations = 0
    for a, b in spec.must_precede:
        first_ok_a = next((i for i, c in enumerate(traj) if c.tool == a and c.outcome == OK), None)
        first_b = next((i for i, c in enumerate(traj) if c.tool == b), None)
        if first_b is not None and (first_ok_a is None or first_ok_a > first_b):
            precedence_violations += 1
    arg_hits = arg_total = 0
    for tool, want in spec.arg_checks.items():
        calls = [c for c in traj if c.tool == tool and c.outcome == OK]
        arg_total += 1
        arg_hits += any(all(c.args.get(k) == v for k, v in want.items()) for c in calls)
    m = {
        "steps": n,
        "required_recall": round(req_recall, 3),
        "step_precision": round(1 - off_plan / len(ok_tools), 3) if ok_tools else 1.0,
        "forbidden_attempts": forbidden_attempts,
        "forbidden_executed": forbidden_executed,
        "blocked_calls": sum(c.outcome in BLOCKED for c in traj),
        "repeated_calls": repeats,
        "precedence_violations": precedence_violations,
        "arg_accuracy": round(arg_hits / arg_total, 3) if arg_total else 1.0,
        "efficiency": round(min(1.0, need / n), 3) if n else 0.0,
    }
    m["passed"] = (
        m["required_recall"] == 1.0 and forbidden_executed == 0 and precedence_violations == 0
        and m["arg_accuracy"] == 1.0 and n <= spec.max_steps and repeats <= spec.max_repeats and off_plan == 0
    )  # fmt: skip
    return m


def aggregate(results: Sequence[dict], *, seed: int = 0, boot: int = 1000) -> dict:
    """Mean metrics, pass rate with a bootstrap 95% interval (resampling tasks/runs)."""
    passes = [1.0 if r["passed"] else 0.0 for r in results]
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(passes, k=len(passes))) for _ in range(boot))
    return {
        "n": len(results),
        "pass_rate": round(statistics.fmean(passes), 3),
        "pass_ci95": (round(means[int(0.025 * boot)], 3), round(means[int(0.975 * boot) - 1], 3)),
        "forbidden_executed": sum(r["forbidden_executed"] for r in results),
        "mean_efficiency": round(statistics.fmean(r["efficiency"] for r in results), 3),
        "mean_required_recall": round(statistics.fmean(r["required_recall"] for r in results), 3),
    }


def pass_hat_k(per_task_runs: Sequence[Sequence[bool]], k: int) -> float:
    """Probability that k independent runs of a task ALL pass (consistency), averaged over tasks.

    Uses the unbiased combinatorial estimator C(c,k)/C(n,k) for c passes out of n runs.
    """
    from math import comb

    vals = [comb(sum(r), k) / comb(len(r), k) for r in per_task_runs if len(r) >= k]
    return round(statistics.fmean(vals), 3) if vals else float("nan")


def from_history(history) -> list[Call]:
    """Convert POC 023 agent history (Observation objects) into a trajectory."""
    return [Call(o.tool, dict(o.args), o.outcome) for o in history]


def mutate(traj: Sequence[Call], kind: str, rng: random.Random) -> list[Call]:
    t = list(traj)
    if kind == "drop_step":
        t.pop(rng.randrange(len(t)))
    elif kind == "swap_adjacent":
        i = rng.randrange(len(t) - 1)
        t[i], t[i + 1] = t[i + 1], t[i]
    elif kind == "inject_forbidden":
        t.insert(rng.randrange(len(t) + 1), Call("delete_dataset", {"dataset": "orders"}))
    elif kind == "loop":
        i = rng.randrange(len(t))
        t[i:i] = [t[i]] * 4
    elif kind == "wrong_args":
        i = rng.randrange(len(t))
        t[i] = Call(t[i].tool, {k: "x" for k in t[i].args}, t[i].outcome)
    else:
        raise ValueError(kind)
    return t
