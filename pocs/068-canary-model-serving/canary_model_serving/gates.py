"""Statistical guardrails comparing canary against baseline at each look; alpha is split across looks (Bonferroni)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats

from .sim import Sample


@dataclass(frozen=True)
class GateConfig:
    alpha: float = 0.05
    looks: int = 20
    max_error_increase: float = 0.005  # practical margin: +0.5 percentage points
    max_p95_ratio: float = 1.2
    max_accuracy_drop: float = 0.02
    min_canary_requests: int = 1500
    min_labelled: int = 300

    @property
    def alpha_look(self) -> float:
        return self.alpha / self.looks


@dataclass(frozen=True)
class Verdict:
    action: str  # continue | advance | rollback
    reason: str = ""


def _diff_ci(x1: int, n1: int, x2: int, n2: int, z: float) -> tuple[float, float, float]:
    """Observed difference p1 - p2 with lower and upper confidence bounds (unpooled standard error)."""
    p1, p2 = x1 / n1, x2 / n2
    se = math.sqrt(max(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2, 1e-12))
    return p1 - p2, p1 - p2 - z * se, p1 - p2 + z * se


def _quantile_ci(x: np.ndarray, q: float, z: float) -> tuple[float, float, float]:
    """Distribution-free CI for a quantile from order statistics (normal approximation to the binomial rank)."""
    xs = np.sort(x)
    n = len(xs)
    half = z * math.sqrt(n * q * (1 - q))
    lo, hi = max(0, int(math.floor(n * q - half))), min(n - 1, int(math.ceil(n * q + half)))
    return float(xs[lo]), float(xs[min(n - 1, int(n * q))]), float(xs[hi])


def evaluate(cfg: GateConfig, canary: Sample, base: Sample) -> Verdict:
    """Non-inferiority style gate.

    rollback: we are confident a regression EXCEEDS the practical margin (lower confidence bound > margin);
    advance : we are confident every regression is WITHIN the margin (upper bound < margin) on all three metrics;
    continue: otherwise (collect more data). Noise alone, or a tolerable change, never triggers a rollback.
    """
    if canary.n < cfg.min_canary_requests or base.n < cfg.min_canary_requests:
        return Verdict("continue", "not enough data")
    z = float(stats.norm.isf(cfg.alpha_look))
    d, lo, hi = _diff_ci(canary.errors, canary.n, base.errors, base.n, z)
    if lo > cfg.max_error_increase:
        return Verdict(
            "rollback", f"error rate +{d * 100:.2f}pp (lower bound +{lo * 100:.2f}pp > margin)"
        )
    c_lo, c_pt, c_hi = _quantile_ci(canary.latencies, 0.95, z)
    b_lo, b_pt, b_hi = _quantile_ci(base.latencies, 0.95, z)
    if c_lo / b_hi > cfg.max_p95_ratio:
        return Verdict(
            "rollback",
            f"p95 latency x{c_pt / b_pt:.2f} (lower bound x{c_lo / b_hi:.2f} > x{cfg.max_p95_ratio})",
        )
    have_labels = canary.labelled >= cfg.min_labelled and base.labelled >= cfg.min_labelled
    acc_ok = False
    if have_labels:
        drop, alo, ahi = _diff_ci(base.correct, base.labelled, canary.correct, canary.labelled, z)
        if alo > cfg.max_accuracy_drop:
            return Verdict(
                "rollback",
                f"accuracy -{drop * 100:.1f}pp on delayed labels (lower bound -{alo * 100:.1f}pp)",
            )
        acc_ok = ahi < cfg.max_accuracy_drop
    proven = hi < cfg.max_error_increase and c_hi / b_lo < cfg.max_p95_ratio and acc_ok
    return Verdict(
        "advance" if proven else "continue", "" if proven else "regression not yet ruled out"
    )
