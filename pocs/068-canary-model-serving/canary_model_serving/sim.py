"""Simulated serving traffic for a baseline model and a candidate with configurable regressions. No model is run."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LABEL_FRACTION = 0.3  # share of requests whose ground truth arrives later (delayed labels)


@dataclass(frozen=True)
class Variant:
    name: str
    error_rate: float = 0.01
    latency_median_ms: float = 80.0
    latency_sigma: float = 0.3
    accuracy: float = 0.92


BASELINE = Variant("baseline")
SCENARIOS = {
    "healthy (identical)": Variant("c1"),
    "mild error rise (1.0% -> 1.3%, inside tolerance)": Variant("c2", error_rate=0.013),
    "latency +35%": Variant("c3", latency_median_ms=108.0),
    "error rate 1% -> 3%": Variant("c4", error_rate=0.03),
    "silent accuracy drop 92% -> 87%": Variant("c5", accuracy=0.87),
    "latency tail blow-up (sigma 0.3 -> 0.7)": Variant("c6", latency_sigma=0.7),
}


@dataclass
class Sample:
    n: int
    errors: int
    latencies: np.ndarray
    labelled: int
    correct: int


def draw(v: Variant, n: int, rng: np.random.Generator) -> Sample:
    if n <= 0:
        return Sample(0, 0, np.empty(0), 0, 0)
    lat = rng.lognormal(np.log(v.latency_median_ms), v.latency_sigma, n)
    lab = rng.binomial(n, LABEL_FRACTION)
    return Sample(
        n, int(rng.binomial(n, v.error_rate)), lat, int(lab), int(rng.binomial(lab, v.accuracy))
    )
