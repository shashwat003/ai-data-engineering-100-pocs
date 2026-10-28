"""Online detectors: update(x, t) -> anomaly score (higher = more anomalous). One pass, bounded memory."""

from __future__ import annotations

import math
import statistics
from collections import deque

from river import anomaly, drift

from .streams import PERIOD


class EwmaZ:
    """Exponentially weighted mean/variance; score = |x - mean| / std. Updates are skipped on very high scores."""

    name = "EWMA z-score"

    def __init__(self, alpha: float = 0.02, freeze_above: float = 6.0, warmup: int = 100):
        self.a, self.freeze, self.warm = alpha, freeze_above, warmup
        self.mean = self.var = None
        self.n = 0

    def update(self, x: float | None, t: int) -> float:
        if x is None:
            return 0.0
        self.n += 1
        if self.mean is None:
            self.mean, self.var = x, 1.0
            return 0.0
        z = abs(x - self.mean) / math.sqrt(self.var + 1e-9)
        if z < self.freeze or self.n < self.warm:
            d = x - self.mean
            self.mean += self.a * d
            self.var = (1 - self.a) * (self.var + self.a * d * d)
        return z if self.n >= self.warm else 0.0


class RobustZ:
    """Rolling median and MAD over a window."""

    name = "Rolling median/MAD"

    def __init__(self, window: int = 200):
        self.buf: deque[float] = deque(maxlen=window)

    def update(self, x: float | None, t: int) -> float:
        if x is None:
            return 0.0
        s = 0.0
        if len(self.buf) >= 50:
            med = statistics.median(self.buf)
            mad = statistics.median(abs(v - med) for v in self.buf) * 1.4826 + 1e-9
            s = abs(x - med) / mad
        if s < 6.0:
            self.buf.append(x)
        return s


class SeasonalEwma:
    """One EWMA mean/variance per phase of the day: expected value and spread for this time of day."""

    name = "Seasonal EWMA"

    def __init__(self, alpha: float = 0.15, period: int = PERIOD, freeze_above: float = 6.0):
        self.a, self.p, self.freeze = alpha, period, freeze_above
        self.mean: list[float | None] = [None] * period
        self.var = [16.0] * period
        self.seen = [0] * period

    def update(self, x: float | None, t: int) -> float:
        if x is None:
            return 0.0
        k = t % self.p
        if self.mean[k] is None:
            self.mean[k] = x
            self.seen[k] = 1
            return 0.0
        z = abs(x - self.mean[k]) / math.sqrt(self.var[k])
        if self.seen[k] >= 2 and z >= self.freeze:
            return z
        d = x - self.mean[k]
        self.mean[k] += self.a * d
        self.var[k] = max(4.0, (1 - self.a) * (self.var[k] + self.a * d * d))
        self.seen[k] += 1
        return z if self.seen[k] > 2 else 0.0


class Hst:
    """river HalfSpaceTrees on [deviation from a slow baseline, first difference, sin/cos of time of day]."""

    name = "river HalfSpaceTrees"

    def __init__(self, seed: int = 0):
        self.model = anomaly.HalfSpaceTrees(n_trees=25, height=8, window_size=250, seed=seed)
        self.prev: float | None = None
        self.base: float | None = None

    def update(self, x: float | None, t: int) -> float:
        if x is None:
            return 0.0
        self.base = x if self.base is None else self.base + 0.05 * (x - self.base)
        f = {
            "dev": (x - self.base) / 60 + 0.5,
            "diff": ((x - self.prev) if self.prev is not None else 0.0) / 60 + 0.5,
            "s": 0.5 + 0.5 * math.sin(2 * math.pi * t / PERIOD),
            "c": 0.5 + 0.5 * math.cos(2 * math.pi * t / PERIOD),
        }
        f = {k: min(1.0, max(0.0, v)) for k, v in f.items()}
        score = self.model.score_one(f)
        self.model.learn_one(f)
        self.prev = x
        return score if t > PERIOD else 0.0


class AdwinShift:
    """river ADWIN on the deseasonalised residual; score 1 for a point where drift is flagged, else 0."""

    name = "river ADWIN (drift)"

    def __init__(self, delta: float = 0.002):
        self.adwin = drift.ADWIN(delta=delta)
        self.seas = SeasonalEwma()

    def update(self, x: float | None, t: int) -> float:
        if x is None:
            return 0.0
        k = t % PERIOD
        mean = self.seas.mean[k]
        self.seas.update(x, t)
        if mean is None:
            return 0.0
        self.adwin.update(x - mean)
        return 1.0 if self.adwin.drift_detected and t > 2 * PERIOD else 0.0


class MissingRun:
    """Data-quality rule, not a statistical model: score = length of the current run of missing points."""

    name = "Missing-data run rule"
    fixed_threshold = 2.5  # three or more consecutive missing points (15 minutes)

    def __init__(self):
        self.run = 0

    def update(self, x: float | None, t: int) -> float:
        self.run = self.run + 1 if x is None else 0
        return float(self.run) if t > 2 * PERIOD else 0.0
