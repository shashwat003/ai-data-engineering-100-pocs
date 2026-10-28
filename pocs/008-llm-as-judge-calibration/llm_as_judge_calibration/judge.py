"""SIMULATED pairwise LLM judge with controllable biases, human labels, metrics and calibration.

No LLM is called. The simulator exists so the *measurement and correction methods* can be tested
against known ground truth (true latent quality, true bias sizes). The same functions accept
judgements from a real judge model: only `SimJudge.prefers_first` would be replaced.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Pairs:
    qa: np.ndarray  # latent quality of answer A
    qb: np.ndarray
    la: np.ndarray  # length (words) of A
    lb: np.ndarray
    human: np.ndarray  # 1 if the human preferred A


def make_pairs(n: int, seed: int = 0, human_noise: float = 0.35) -> Pairs:
    rng = np.random.default_rng(seed)
    qa, qb = rng.normal(0, 1, n), rng.normal(0, 1, n)
    la = np.clip(
        60 + 25 * qa * 0.3 + rng.normal(0, 30, n), 10, None
    )  # length weakly correlated with quality
    lb = np.clip(60 + 25 * qb * 0.3 + rng.normal(0, 30, n), 10, None)
    human = (qa - qb + rng.normal(0, human_noise, n) > 0).astype(int)
    return Pairs(qa, qb, la, lb, human)


@dataclass
class SimJudge:
    sharpness: float = 2.5  # how strongly it tracks true quality difference
    position_bias: float = 0.0  # logit added when the answer is shown FIRST
    verbosity_bias: float = 0.0  # logit per +100 words of length advantage
    noise: float = 0.5  # judgement noise (logit scale)
    seed: int = 0

    def logit_first(self, q1, q2, l1, l2, rng) -> np.ndarray:
        return (
            self.sharpness * (q1 - q2)
            + self.position_bias
            + self.verbosity_bias * (l1 - l2) / 100
            + rng.normal(0, self.noise, len(q1))
        )

    def judge(self, p: Pairs, order: str = "ab") -> np.ndarray:
        """Returns logit that the item shown first is better. order 'ab' shows A first; 'ba' shows B first."""
        rng = np.random.default_rng(self.seed + (0 if order == "ab" else 1))
        if order == "ab":
            return self.logit_first(p.qa, p.qb, p.la, p.lb, rng)
        return self.logit_first(p.qb, p.qa, p.lb, p.la, rng)


def prefers_a(
    judge: SimJudge, p: Pairs, mode: str = "single"
) -> tuple[np.ndarray, np.ndarray | None]:
    """mode: single (A first), swap_avg (average evidence over both orders), swap_agree (abstain on disagreement).
    Returns (prediction in {0,1}, mask of answered items or None)."""
    ab = judge.judge(p, "ab")
    if mode == "single":
        return (ab > 0).astype(int), None
    ba = judge.judge(p, "ba")  # logit that B (shown first) is better
    if mode == "swap_avg":
        return ((ab - ba) > 0).astype(int), None
    agree = (ab > 0) == (ba < 0)
    return (ab > 0).astype(int), agree


def accuracy(pred, human, mask=None) -> float:
    m = np.ones(len(pred), bool) if mask is None else mask
    return float((pred[m] == human[m]).mean()) if m.any() else float("nan")


def kappa(pred, human) -> float:
    po = (pred == human).mean()
    pe = pred.mean() * human.mean() + (1 - pred.mean()) * (1 - human.mean())
    return float((po - pe) / (1 - pe)) if pe < 1 else 1.0


def position_bias_estimate(judge: SimJudge, p: Pairs) -> float:
    """P(first-shown wins) minus 0.5 over BOTH orders: in an unbiased judge each item wins first-position half the time."""
    ab = judge.judge(p, "ab") > 0
    ba = judge.judge(p, "ba") > 0
    return float((ab.mean() + ba.mean()) / 2 - 0.5)


def fit_logistic(
    X: np.ndarray, y: np.ndarray, l2: float = 1e-2, iters: int = 400, lr: float = 0.3
) -> np.ndarray:
    Xb = np.hstack([X, np.ones((len(X), 1))])
    w = np.zeros(Xb.shape[1])
    for _ in range(iters):
        pr = 1 / (1 + np.exp(-(Xb @ w)))
        w -= lr * (Xb.T @ (pr - y) / len(y) + l2 * w)
    return w


def features(judge: SimJudge, p: Pairs) -> np.ndarray:
    ab = judge.judge(p, "ab")
    return np.column_stack([ab, (p.la - p.lb) / 100])


def predict(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    return (np.hstack([X, np.ones((len(X), 1))]) @ w > 0).astype(int)


def bootstrap_ci(values: np.ndarray, seed: int = 0, n: int = 500) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    means = np.sort([rng.choice(values, len(values)).mean() for _ in range(n)])
    return float(means[int(0.025 * n)]), float(means[int(0.975 * n) - 1])


def kendall_tau(a: list[float], b: list[float]) -> float:
    n, s = len(a), 0
    for i in range(n):
        for j in range(i + 1, n):
            s += np.sign(a[i] - a[j]) * np.sign(b[i] - b[j])
    return float(s / (n * (n - 1) / 2))
