"""Batch-inference scheduling under a spot-price curve, deadlines, interruptions and a capacity limit.

Time is hourly over a 48 h horizon. A job needs `work` slot-hours, can use at most `par` slots in any
hour, may start at `release` and must finish by `deadline`. Spot slots cost the hourly spot price but
can be interrupted (the hour's progress is lost); on-demand slots cost a fixed higher price and never are.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog

HORIZON, CAPACITY, ON_DEMAND = 48, 8, 3.0


@dataclass(frozen=True)
class Job:
    id: int
    work: int  # slot-hours of compute
    par: int  # max parallel slots
    release: int
    deadline: int  # hour by which all work must be done (exclusive)


def spot_prices(rng, horizon: int = HORIZON) -> np.ndarray:
    """Diurnal curve (cheap at night) with random spikes, capped below the on-demand price."""
    h = np.arange(horizon)
    base = 1.0 + 0.5 * np.sin((h % 24 - 6) / 24 * 2 * np.pi)
    spikes = (rng.random(horizon) < 0.08) * rng.uniform(1.0, 2.2, horizon)
    return np.minimum(base + spikes + rng.normal(0, 0.05, horizon), ON_DEMAND * 0.95).clip(0.3)


def make_jobs(rng, n: int = 12) -> list[Job]:
    jobs = []
    for i in range(n):
        work, par = int(rng.integers(4, 20)), int(rng.integers(2, 5))
        release = int(rng.integers(0, 12))
        min_len = -(-work // par)
        deadline = min(HORIZON, release + min_len + int(rng.integers(3, 30)))
        jobs.append(Job(i, work, par, release, max(deadline, release + min_len + 1)))
    return jobs


def lp_lower_bound(jobs: list[Job], price: np.ndarray) -> float:
    """Cheapest feasible schedule if prices are known in advance and spot never interrupts (a lower bound)."""
    nj, T = len(jobs), len(price)
    c = np.tile(price, nj)
    A_eq = np.zeros((nj, nj * T))
    bounds = []
    for j, job in enumerate(jobs):
        A_eq[j, j * T : (j + 1) * T] = 1
        for h in range(T):
            bounds.append((0, job.par if job.release <= h < job.deadline else 0))
    A_ub = np.zeros((T, nj * T))
    for h in range(T):
        A_ub[h, h::T] = 1
    res = linprog(
        c,
        A_ub=A_ub,
        b_ub=np.full(T, CAPACITY),
        A_eq=A_eq,
        b_eq=[j.work for j in jobs],
        bounds=bounds,
        method="highs",
    )
    return float(res.fun) if res.status == 0 else float("inf")


def simulate(
    policy: str, jobs: list[Job], price: np.ndarray, rng, q_interrupt: float = 0.12
) -> dict:
    remaining = {j.id: float(j.work) for j in jobs}
    done_at: dict[int, int] = {}
    cost = lost = 0.0
    peak = 0
    for h in range(len(price)):
        free = CAPACITY
        active = [j for j in jobs if j.release <= h and remaining[j.id] > 1e-9]
        if policy in ("edf_cost_aware", "edf_on_demand"):
            active.sort(key=lambda j: j.deadline)
        else:
            active.sort(key=lambda j: (j.release, j.id))
        plan: list[tuple[Job, int, bool]] = []
        if policy in ("fifo_on_demand", "edf_on_demand", "fifo_spot"):
            for j in active:
                use = min(j.par, free, int(np.ceil(remaining[j.id])))
                if use > 0:
                    plan.append((j, use, policy != "fifo_spot"))
                    free -= use
        else:
            # pass 1: jobs that would miss their deadline without running now go first, on on-demand (safety margin 0.6
            # absorbs spot interruptions); pass 2: everything else runs on spot only in cheap hours (<= 40th percentile of the remaining window)
            for j in active:
                rem, time_left = remaining[j.id], j.deadline - h
                if rem > (time_left - 1) * j.par * 0.6 and free > 0:
                    use = min(j.par, free, int(np.ceil(rem)))
                    plan.append((j, use, True))
                    free -= use
            for j in active:
                if any(p[0].id == j.id for p in plan) or free <= 0:
                    continue
                if price[h] <= np.percentile(price[h : j.deadline], 40):
                    use = min(j.par, free, int(np.ceil(remaining[j.id])))
                    plan.append((j, use, False))
                    free -= use
        peak = max(peak, sum(u for _, u, _ in plan))
        for j, use, od in plan:
            cost += use * (ON_DEMAND if od else price[h])
            if not od and rng.random() < q_interrupt:
                lost += (
                    use  # interrupted: billed, no progress (hourly checkpoints keep earlier work)
                )
                continue
            remaining[j.id] -= use
            if remaining[j.id] <= 1e-9:
                done_at[j.id] = h + 1
    missed = sum(1 for j in jobs if done_at.get(j.id, 10**9) > j.deadline)
    return {"cost": cost, "missed": missed, "lost_slot_hours": lost, "peak_slots": peak}
