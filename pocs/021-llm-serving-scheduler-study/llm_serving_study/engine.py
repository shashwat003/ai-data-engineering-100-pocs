"""Discrete-event model of an LLM serving engine: prefill + decode iterations, KV-cache limits, three scheduling policies.

Cost model (ASSUMED, illustrative of a 7B model on one GPU; no GPU was used):
  decode iteration time  = a + b * (sequences in the batch)       (memory-bound: weights are read once per iteration)
  prefill time           = c * prompt tokens                       (compute-bound)
KV memory is counted in tokens: `reserved` allocates prompt + max_output tokens up front per sequence; `paged` allocates
16-token blocks as the sequence grows (the idea behind PagedAttention) and preempts the newest sequence if it runs out.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

BLOCK = 16


@dataclass(frozen=True)
class Model:
    a: float = 0.018  # s per decode iteration
    b: float = 0.0004  # extra s per sequence in the batch
    c: float = 0.00025  # s per prompt token
    kv_tokens: int = 40_000
    max_output: int = 512
    max_batch: int = 64


@dataclass
class Request:
    rid: int
    arrival: float
    prompt: int
    output: int
    generated: int = 0
    first_token: float | None = None
    finish: float | None = None
    preemptions: int = 0


@dataclass
class Metrics:
    done: list[Request]
    rejected: int
    horizon: float
    busy: float
    kv_peak_tokens: int
    max_concurrency: int
    preemptions: int

    def summary(self) -> dict:
        d = self.done
        if not d:
            return {"completed": 0}
        ttft = np.array([r.first_token - r.arrival for r in d])
        e2e = np.array([r.finish - r.arrival for r in d])
        tpot = np.array([(r.finish - r.first_token) / max(r.output - 1, 1) for r in d])
        span = max(r.finish for r in d)
        return {
            "completed": len(d),
            "rejected": self.rejected,
            "throughput_tok_s": round(sum(r.output for r in d) / span, 1),
            "ttft_p50_s": round(float(np.percentile(ttft, 50)), 3),
            "ttft_p95_s": round(float(np.percentile(ttft, 95)), 3),
            "tpot_p50_ms": round(float(np.percentile(tpot, 50)) * 1000, 1),
            "e2e_p95_s": round(float(np.percentile(e2e, 95)), 2),
            "gpu_busy_pct": round(100 * self.busy / span, 1),
            "max_concurrency": self.max_concurrency,
            "kv_peak_pct": round(100 * self.kv_peak_tokens / 40_000, 1),
            "preemptions": self.preemptions,
        }


def workload(
    rate: float,
    duration: float,
    seed: int = 0,
    prompt_median: int = 300,
    output_median: int = 120,
    max_output: int = 512,
) -> list[Request]:
    rng = np.random.default_rng(seed)
    t, out, rid = 0.0, [], 0
    while True:
        t += rng.exponential(1 / rate)
        if t > duration:
            return out
        p = int(np.clip(rng.lognormal(math.log(prompt_median), 0.7), 8, 1800))
        o = int(np.clip(rng.lognormal(math.log(output_median), 0.8), 4, max_output))
        out.append(Request(rid, t, p, o))
        rid += 1


def _blocks(tokens: int) -> int:
    return -(-tokens // BLOCK)


def simulate(
    reqs: list[Request],
    model: Model,
    policy: str,
    kv: str = "paged",
    static_batch: int = 8,
    static_wait: float = 0.25,
) -> Metrics:
    """policy: none | static | continuous.  kv: reserved | paged (only meaningful for continuous; static/none reserve)."""
    assert policy in ("none", "static", "continuous") and kv in ("reserved", "paged")
    reqs = [Request(r.rid, r.arrival, r.prompt, r.output) for r in reqs]
    pending = deque(sorted(reqs, key=lambda r: r.arrival))
    waiting: deque[Request] = deque()
    active: list[Request] = []
    t, busy, done, rejected = 0.0, 0.0, [], 0
    kv_peak = max_conc = preempt = 0
    cap = model.kv_tokens

    def kv_used() -> int:
        if kv == "reserved" or policy != "continuous":
            return sum(r.prompt + model.max_output for r in active)
        return sum(_blocks(r.prompt + r.generated + 1) for r in active) * BLOCK

    def need(r: Request) -> int:
        return (
            r.prompt + model.max_output
            if (kv == "reserved" or policy != "continuous")
            else _blocks(r.prompt + 1) * BLOCK
        )

    while pending or waiting or active:
        while pending and pending[0].arrival <= t:
            r = pending.popleft()
            if r.prompt + r.output > cap:
                rejected += 1
            else:
                waiting.append(r)
        if not active and not waiting:
            t = max(t, pending[0].arrival)
            continue
        if policy == "static" and not active and waiting:
            deadline = (
                waiting[0].arrival + static_wait
            )  # wait for a full batch, but never longer than static_wait
            if len(waiting) < static_batch and t < deadline:
                t = min(deadline, pending[0].arrival if pending else math.inf)
                continue
        # admission
        prefill = 0.0
        cap_batch = (
            1 if policy == "none" else (static_batch if policy == "static" else model.max_batch)
        )
        if (
            policy != "static" or not active
        ):  # static batching admits only when the previous batch has fully finished
            admitted: list[Request] = []
            while waiting and len(active) + len(admitted) < cap_batch:
                r = waiting[0]
                if kv_used() + sum(need(x) for x in admitted) + need(r) > cap:
                    break
                admitted.append(waiting.popleft())
            prefill = sum(model.c * r.prompt for r in admitted)
            active += admitted
        if not active:
            t = pending[0].arrival if pending else t + 1e-3
            continue
        if (
            kv == "paged" and policy == "continuous"
        ):  # room for one more token per sequence, else preempt the newest (recompute later)
            while (
                len(active) > 1
                and sum(_blocks(r.prompt + r.generated + 2) for r in active) * BLOCK > cap
            ):
                victim = active.pop()
                victim.generated, victim.first_token = 0, None
                victim.preemptions += 1
                preempt += 1
                waiting.appendleft(victim)
        step = model.a + model.b * len(active)
        t += prefill + step
        busy += prefill + step
        kv_peak = max(kv_peak, kv_used())
        max_conc = max(max_conc, len(active))
        finished = []
        for r in active:
            r.generated += 1
            if r.first_token is None:
                r.first_token = t
            if r.generated >= r.output:
                r.finish = t
                finished.append(r)
        for r in finished:
            active.remove(r)
            done.append(r)
    return Metrics(done, rejected, t, busy, kv_peak, max_conc, preempt)


@dataclass
class Sweep:
    rows: list[dict] = field(default_factory=list)
