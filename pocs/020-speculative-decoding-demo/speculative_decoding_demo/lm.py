"""Toy language models over a small vocabulary: exact next-token distributions, so correctness can be checked exactly."""

from __future__ import annotations

import numpy as np


class TrigramLM:
    """p(next | previous two tokens) from a seeded Dirichlet table. Context is the last two tokens (padded with token 0)."""

    def __init__(self, vocab: int = 30, concentration: float = 0.15, seed: int = 0):
        rng = np.random.default_rng(seed)
        self.v = vocab
        self.table = rng.dirichlet(
            np.full(vocab, concentration), size=(vocab, vocab)
        )  # (prev2, prev1, next)
        self.calls = 0

    def probs(self, context: list[int]) -> np.ndarray:
        a = context[-2] if len(context) >= 2 else 0
        b = context[-1] if context else 0
        return self.table[a, b]

    def probs_batch(self, context: list[int], extra: list[int]) -> list[np.ndarray]:
        """Distributions after context, context+extra[:1], ..., context+extra: len(extra)+1 positions in ONE call (parallel verification)."""
        self.calls += 1
        return [self.probs(context + extra[:i]) for i in range(len(extra) + 1)]


class BigramDraft:
    """A cheaper, approximate model: conditions on the last token only. `mix` interpolates with the exact target marginal."""

    def __init__(self, target: TrigramLM, mix: float = 0.0, noise: float = 0.0, seed: int = 1):
        rng = np.random.default_rng(seed)
        marg = target.table.mean(0)  # average over the older context token: the best bigram summary
        base = (1 - mix) * marg + mix * np.tile(target.table.mean((0, 1)), (target.v, 1))
        if noise:
            base = base * rng.lognormal(0, noise, base.shape)
        self.table = base / base.sum(1, keepdims=True)
        self.v = target.v
        self.calls = 0

    def probs(self, context: list[int]) -> np.ndarray:
        return self.table[context[-1] if context else 0]


class ExactDraft:
    """A draft identical to the target (acceptance must be 1)."""

    def __init__(self, target: TrigramLM):
        self.t = target
        self.v = target.v
        self.calls = 0

    def probs(self, context):
        return self.t.probs(context)


class NoisyCopyDraft:
    """A smaller model that is a perturbed copy of the target: p'(x) proportional to p(x) * lognormal(sigma). sigma controls draft quality."""

    def __init__(self, target: TrigramLM, sigma: float, seed: int = 2):
        rng = np.random.default_rng(seed)
        t = target.table * rng.lognormal(0, sigma, target.table.shape)
        self.table = t / t.sum(-1, keepdims=True)
        self.v = target.v
        self.calls = 0

    def probs(self, context):
        a = context[-2] if len(context) >= 2 else 0
        b = context[-1] if context else 0
        return self.table[a, b]
