"""Autoregressive decoding and speculative decoding (Leviathan et al. 2023; Chen et al. 2023) on toy models."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .lm import TrigramLM


@dataclass
class Stats:
    tokens: int = 0
    target_calls: int = 0
    draft_calls: int = 0
    proposed: int = 0
    verified: int = (
        0  # proposals actually examined (up to and including the first rejection in each call)
    )
    accepted: int = 0
    emitted: int = 0  # tokens produced before the final call is trimmed to the requested length

    @property
    def acceptance(self) -> float:
        """Per-token acceptance probability estimate: accepted / examined."""
        return self.accepted / self.verified if self.verified else 0.0

    @property
    def emitted_per_target_call(self) -> float:
        return self.emitted / self.target_calls if self.target_calls else 0.0

    @property
    def tokens_per_target_call(self) -> float:
        return self.tokens / self.target_calls if self.target_calls else 0.0


def sample(p: np.ndarray, rng: np.random.Generator) -> int:
    return int(rng.choice(len(p), p=p))


def autoregressive(
    target: TrigramLM,
    n: int,
    rng: np.random.Generator,
    prompt: list[int] | None = None,
    greedy: bool = False,
) -> tuple[list[int], Stats]:
    out, st = list(prompt or []), Stats()
    for _ in range(n):
        (p,) = target.probs_batch(out, [])
        out.append(int(np.argmax(p)) if greedy else sample(p, rng))
        st.target_calls += 1
        st.tokens += 1
    return out[len(prompt or []) :], st


def speculative(
    target: TrigramLM,
    draft,
    n: int,
    k: int,
    rng: np.random.Generator,
    prompt: list[int] | None = None,
    greedy: bool = False,
) -> tuple[list[int], Stats]:
    """Draft k tokens, verify them with ONE target call, accept a prefix, always emit at least one token per target call."""
    ctx, st = list(prompt or []), Stats()
    start = len(ctx)
    while len(ctx) - start < n:
        guesses, qs = [], []
        for _ in range(k):  # draft proposes autoregressively
            q = draft.probs(ctx + guesses)
            g = int(np.argmax(q)) if greedy else sample(q, rng)
            guesses.append(g)
            qs.append(q)
            st.draft_calls += 1
        ps = target.probs_batch(ctx, guesses)  # one parallel verification pass over k+1 positions
        st.target_calls += 1
        st.proposed += k
        emitted = []
        for i, g in enumerate(guesses):
            p, q = ps[i], qs[i]
            if greedy:
                ok = int(np.argmax(p)) == g
            else:
                ok = rng.random() < min(1.0, p[g] / q[g]) if q[g] > 0 else False
            st.verified += 1
            if ok:
                emitted.append(g)
                st.accepted += 1
                continue
            if greedy:
                emitted.append(int(np.argmax(p)))
            else:
                resid = np.maximum(p - q, 0.0)
                total = resid.sum()
                emitted.append(
                    sample(resid / total if total > 0 else p, rng)
                )  # sample from the normalised residual distribution
            break
        else:
            emitted.append(
                int(np.argmax(ps[k])) if greedy else sample(ps[k], rng)
            )  # all accepted: bonus token from the last position
        st.emitted += len(emitted)
        take = min(len(emitted), n - (len(ctx) - start))
        ctx += emitted[:take]
        st.tokens += take
    return ctx[start:], st


def expected_tokens_per_call(alpha: float, k: int) -> float:
    """E[tokens per target call] = (1 - alpha^(k+1)) / (1 - alpha), for i.i.d. per-token acceptance probability alpha."""
    return float(k + 1) if alpha >= 1 else (1 - alpha ** (k + 1)) / (1 - alpha)


def speedup(alpha: float, k: int, cost_ratio: float) -> float:
    """Modelled wall-clock speedup vs autoregressive: tokens per call / (1 target pass + k draft passes at `cost_ratio` each)."""
    return expected_tokens_per_call(alpha, k) / (1 + k * cost_ratio)
