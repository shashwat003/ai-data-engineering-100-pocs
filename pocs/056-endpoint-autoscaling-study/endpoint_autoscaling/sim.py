"""Discrete-event simulation of an inference endpoint: a multi-server FCFS queue whose capacity is changed by an autoscaler.

Servers are worker slots (instances x workers per instance). Requests wait in one shared queue (join-the-first-free-server),
time out after `queue_timeout_s`, and are served with log-normal service times. Instances take `startup_s` to become usable.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Config:
    workers_per_instance: int = 4
    service_mean_s: float = 0.08
    service_sigma: float = 0.4
    startup_s: float = 180.0
    queue_timeout_s: float = 5.0
    slo_s: float = 0.3
    price_per_instance_hour: float = 0.23  # ASSUMED price, illustrative only
    exponential_service: bool = False  # exponential service times (for the M/M/c validation)
    seed: int = 0


@dataclass
class Result:
    latencies: np.ndarray
    dropped: int
    total: int
    instance_seconds: float
    scale_events: int
    capacity_trace: list[tuple[float, int]] = field(default_factory=list)

    def summary(self, cfg: Config) -> dict:
        lat = self.latencies
        served = len(lat)
        return {
            "requests": self.total,
            "p50_ms": round(float(np.percentile(lat, 50)) * 1000, 1) if served else None,
            "p95_ms": round(float(np.percentile(lat, 95)) * 1000, 1) if served else None,
            "p99_ms": round(float(np.percentile(lat, 99)) * 1000, 1) if served else None,
            "slo_violation_pct": round(
                100 * (float((lat > cfg.slo_s).sum()) + self.dropped) / max(self.total, 1), 2
            ),
            "dropped_pct": round(100 * self.dropped / max(self.total, 1), 2),
            "instance_hours": round(self.instance_seconds / 3600, 2),
            "cost_usd": round(self.instance_seconds / 3600 * cfg.price_per_instance_hour, 2),
            "scale_events": self.scale_events,
        }


def lognormal_params(mean: float, sigma: float) -> tuple[float, float]:
    return math.log(mean) - sigma**2 / 2, sigma


def arrivals(rate_fn, horizon_s: float, seed: int) -> np.ndarray:
    """Non-homogeneous Poisson arrivals by thinning against the maximum rate."""
    rng = np.random.default_rng(seed)
    grid = np.arange(0, horizon_s, 10.0)
    lam_max = max(rate_fn(t) for t in grid) * 1.05
    t, out = 0.0, []
    while t < horizon_s:
        t += rng.exponential(1 / lam_max)
        if t < horizon_s and rng.random() < rate_fn(t) / lam_max:
            out.append(t)
    return np.array(out)


def simulate(
    cfg: Config,
    arr: np.ndarray,
    policy,
    horizon_s: float,
    tick_s: float = 10.0,
    initial_instances: int = 1,
) -> Result:
    """policy(t, state) -> desired instance count, evaluated every `tick_s` seconds.

    state: rate (req/s over the last tick), rate_per_instance, util (busy fraction of worker slots over the last tick),
    instances (active), starting (launched, not yet usable), workers.
    """
    rng = np.random.default_rng(cfg.seed)
    mu, sg = lognormal_params(cfg.service_mean_s, cfg.service_sigma)
    w = cfg.workers_per_instance
    free = [0.0] * (initial_instances * w)  # moment each ACTIVE worker slot becomes free
    heapq.heapify(free)
    active, starting = initial_instances, []  # starting: heap of (ready_time, count)
    lat, dropped, events = [], 0, 0
    trace = [(0.0, active)]
    billed, last_t = 0.0, 0.0
    win_arrivals, win_busy = 0, 0.0
    i, n, next_tick = 0, len(arr), tick_s
    while True:
        t_arr = arr[i] if i < n else math.inf
        t_ready = starting[0][0] if starting else math.inf
        t = min(t_arr, next_tick, t_ready)
        if t > horizon_s:
            break
        billed += (active + sum(k for _, k in starting)) * (t - last_t)
        last_t = t
        if t_ready <= t and t_ready <= next_tick and t_ready <= t_arr:
            _, k = heapq.heappop(starting)
            active += k
            for _ in range(k * w):
                heapq.heappush(free, t)
            trace.append((t, active))
        elif next_tick <= t_arr:
            slots = max(len(free), 1)
            state = {
                "t": t,
                "rate": win_arrivals / tick_s,
                "rate_per_instance": win_arrivals / tick_s / max(active, 1),
                "util": min(1.0, win_busy / (tick_s * slots)),
                "instances": active,
                "starting": sum(k for _, k in starting),
                "workers": w,
            }
            desired = int(policy(t, state))
            in_flight = active + state["starting"]
            if desired > in_flight:
                heapq.heappush(starting, (t + cfg.startup_s, desired - in_flight))
                events += 1
            elif desired < active:
                free.sort()
                del free[
                    : (active - desired) * w
                ]  # graceful: release the slots that free up soonest
                heapq.heapify(free)
                active = desired
                events += 1
                trace.append((t, active))
            win_arrivals, win_busy = 0, 0.0
            next_tick += tick_s
        else:
            i += 1
            win_arrivals += 1
            if not free:
                dropped += 1
                continue
            start = max(t, free[0])
            if start - t > cfg.queue_timeout_s:
                dropped += 1
                continue
            heapq.heappop(free)
            svc = (
                float(rng.exponential(cfg.service_mean_s))
                if cfg.exponential_service
                else float(rng.lognormal(mu, sg))
            )
            heapq.heappush(free, start + svc)
            win_busy += svc
            lat.append(start + svc - t)
    billed += (active + sum(k for _, k in starting)) * max(0.0, horizon_s - last_t)
    return Result(np.array(lat), dropped, n, billed, events, trace)


def erlang_c_wait(lam: float, mu: float, c: int) -> float:
    """Mean queueing delay of an M/M/c queue (exponential service): Erlang C. Requires lam < c * mu."""
    a = lam / mu
    rho = a / c
    if rho >= 1:
        return math.inf
    s = sum(a**k / math.factorial(k) for k in range(c))
    top = a**c / (math.factorial(c) * (1 - rho))
    p_wait = top / (s + top)
    return p_wait / (c * mu - lam)
