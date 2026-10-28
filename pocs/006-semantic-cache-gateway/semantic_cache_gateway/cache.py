"""Semantic response cache for an LLM gateway with safety guards.

A hit requires: same tenant, same model + prompt-template version, unexpired entry, cosine similarity
above a threshold, AND agreement on the "facts that change the answer" (numbers and capitalised
entities). The last guard is what stops "refund for order 1042" from returning the answer for
"refund for order 2051". Responses flagged as errors/refusals are never cached.
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from idempotent_vector_ingestion.store import HashingEmbedder

NUM = re.compile(r"\d+(?:\.\d+)?")
ENT = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")
NEVER_CACHE = ("i can't help", "error:", "rate limit", "as an ai")


STARTERS = frozenset(
    "how what where when why who which does do is are can could will would should refund track change warranty order please".split()
)


def facts(prompt: str) -> frozenset[str]:
    """Numbers and capitalised names that decide the answer. Sentence-initial question words are ignored."""
    ents = {e.lower() for e in ENT.findall(prompt) if e.lower() not in STARTERS}
    return frozenset(NUM.findall(prompt)) | frozenset(ents)


@dataclass
class Entry:
    vec: np.ndarray
    facts: frozenset[str]
    prompt: str
    response: str
    created: float
    hits: int = 0


class SemanticCache:
    def __init__(self, embedder: HashingEmbedder | None = None, *, threshold: float = 0.8, ttl_s: float = 3600, max_entries: int = 1000,
                 fact_guard: bool = True, clock: Callable[[], float] = time.monotonic):  # fmt: skip
        self.emb, self.threshold, self.ttl, self.max, self.fact_guard, self.clock = (
            embedder or HashingEmbedder(),
            threshold,
            ttl_s,
            max_entries,
            fact_guard,
            clock,
        )
        self.store: dict[tuple, OrderedDict[int, Entry]] = {}
        self._next = 0
        self.stats = {
            "lookups": 0,
            "hits": 0,
            "misses": 0,
            "rejected_by_fact_guard": 0,
            "expired": 0,
            "evicted": 0,
            "not_cached": 0,
        }

    def _bucket(self, tenant: str, model: str, template: str) -> OrderedDict:
        return self.store.setdefault((tenant, model, template), OrderedDict())

    def lookup(
        self, prompt: str, *, tenant: str, model: str, template: str = "v1"
    ) -> tuple[str | None, float]:
        self.stats["lookups"] += 1
        b = self._bucket(tenant, model, template)
        now = self.clock()
        for k in [k for k, e in b.items() if now - e.created > self.ttl]:
            del b[k]
            self.stats["expired"] += 1
        if not b:
            self.stats["misses"] += 1
            return None, 0.0
        q = self.emb.embed([prompt])[0]
        self.emb.calls -= 1
        keys = list(b)
        sims = np.array([b[k].vec @ q for k in keys])
        order = np.argsort(-sims)
        pf = facts(prompt)
        for i in order:
            if sims[i] < self.threshold:
                break
            e = b[keys[i]]
            if self.fact_guard and e.facts != pf:
                self.stats["rejected_by_fact_guard"] += 1
                continue
            e.hits += 1
            b.move_to_end(keys[i])
            self.stats["hits"] += 1
            return e.response, float(sims[i])
        self.stats["misses"] += 1
        return None, float(sims[order[0]])

    def store_response(
        self, prompt: str, response: str, *, tenant: str, model: str, template: str = "v1"
    ) -> bool:
        if any(m in response.lower() for m in NEVER_CACHE) or not response.strip():
            self.stats["not_cached"] += 1
            return False
        b = self._bucket(tenant, model, template)
        vec = self.emb.embed([prompt])[0]
        self.emb.calls -= 1
        b[self._next] = Entry(vec, facts(prompt), prompt, response, self.clock())
        self._next += 1
        while len(b) > self.max:
            b.popitem(last=False)
            self.stats["evicted"] += 1
        return True


class Gateway:
    """Wraps a model callable; counts tokens and money saved by cache hits."""

    def __init__(
        self,
        model_fn: Callable[[str], str],
        cache: SemanticCache,
        *,
        model: str = "m1",
        usd_per_1k_tokens: float = 0.002,
    ):
        self.fn, self.cache, self.model, self.price = model_fn, cache, model, usd_per_1k_tokens
        self.calls = self.tokens_billed = self.tokens_saved = 0

    def ask(self, prompt: str, tenant: str = "t1") -> tuple[str, bool]:
        hit, _ = self.cache.lookup(prompt, tenant=tenant, model=self.model)
        n = len(prompt.split()) * 4 // 3 + 60  # rough token estimate of prompt + typical answer
        if hit is not None:
            self.tokens_saved += n
            return hit, True
        out = self.fn(prompt)
        self.calls += 1
        self.tokens_billed += n
        self.cache.store_response(prompt, out, tenant=tenant, model=self.model)
        return out, False

    @property
    def usd_saved(self) -> float:
        return round(self.tokens_saved / 1000 * self.price, 4)
