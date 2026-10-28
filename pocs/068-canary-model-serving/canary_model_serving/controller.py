"""Progressive delivery controller: step traffic up, evaluate guardrails at every look, roll back on breach."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .gates import GateConfig, Verdict, evaluate
from .sim import BASELINE, Sample, Variant, draw

STEPS = (5, 20, 50, 100)  # percent of traffic to the canary
PROOF_STEP = (
    50  # the step that must PROVE non-inferiority before 100% (earlier steps are smoke stages)
)
PROOF_DEADLINE_MIN = 120
REQUESTS_PER_MINUTE = 600
LOOK_MINUTES = 5


@dataclass
class Outcome:
    result: str  # promoted | rolled_back
    minutes: int
    reason: str = ""
    canary_requests: int = 0
    excess_errors: float = (
        0.0  # errors beyond what the baseline would have produced on the same requests
    )
    timeline: list[tuple[int, int, str]] = field(default_factory=list)


def _merge(a: Sample, b: Sample) -> Sample:
    return Sample(
        a.n + b.n,
        a.errors + b.errors,
        np.concatenate([a.latencies, b.latencies]),
        a.labelled + b.labelled,
        a.correct + b.correct,
    )


def rollout(
    cand: Variant,
    rng: np.random.Generator,
    cfg: GateConfig | None = None,
    steps=STEPS,
    max_minutes: int = 240,
    base: Variant = BASELINE,
) -> Outcome:
    cfg = cfg or GateConfig()
    cum_c, cum_b = draw(base, 0, rng), draw(base, 0, rng)
    minutes, excess, served, out = 0, 0.0, 0, Outcome("promoted", 0)
    for pct in steps:
        cum_c, cum_b = (
            draw(base, 0, rng),
            draw(base, 0, rng),
        )  # statistics restart at every step (fresh traffic mix)
        looks = 0
        while True:
            n = REQUESTS_PER_MINUTE * LOOK_MINUTES
            nc = int(n * pct / 100)
            c, b = draw(cand, nc, rng), draw(base, n - nc, rng)
            minutes += LOOK_MINUTES
            looks += 1
            served += nc
            excess += max(0.0, nc * (cand.error_rate - base.error_rate))
            if pct == 100:  # fully promoted: nothing left to compare; finish after the bake window
                out.timeline.append((minutes, pct, "promoted"))
                out.minutes, out.canary_requests, out.excess_errors = minutes, served, excess
                return out
            cum_c, cum_b = _merge(cum_c, c), _merge(cum_b, b)
            v: Verdict = evaluate(cfg, cum_c, cum_b)
            out.timeline.append((minutes, pct, v.action))
            if v.action == "rollback":
                return Outcome("rolled_back", minutes, v.reason, served, excess, out.timeline)
            smoke_ok = (
                pct != PROOF_STEP
                and cum_c.n >= cfg.min_canary_requests
                and cum_c.labelled >= cfg.min_labelled
            )
            if smoke_ok or (pct == PROOF_STEP and v.action == "advance"):
                break  # enough evidence at this step: advance
            if looks > (PROOF_DEADLINE_MIN if pct == PROOF_STEP else max_minutes) // LOOK_MINUTES:
                return Outcome(
                    "rolled_back",
                    minutes,
                    "no decision before deadline (fail closed)",
                    served,
                    excess,
                    out.timeline,
                )
    return out


def big_bang(cand: Variant, window_minutes: int = 60, base: Variant = BASELINE) -> dict:
    """All traffic switches at once: every request in the window hits the candidate."""
    n = REQUESTS_PER_MINUTE * window_minutes
    return {
        "requests_on_candidate": n,
        "excess_errors": n * max(0.0, cand.error_rate - base.error_rate),
    }
