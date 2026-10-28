"""Long-context stuffing vs RAG: measured context sizes and retrieval, modelled cost, latency and reader.

MEASURED here: token counts (4 chars/token heuristic), whether the gold document is in the context,
retrieval wall time. MODELLED (assumptions, all parameters explicit): price, prefill/decode speed and
how reader accuracy degrades as the context grows. No LLM is called.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from data_gen import generate_knowledge_base
from llm_client import estimate_tokens
from retrieval_kit import BM25Retriever, DenseRetriever, HybridRetriever, LSAEmbedder


@dataclass(frozen=True)
class Assumptions:
    """Illustrative numbers, NOT vendor quotes. Change them; the conclusions move with them."""

    usd_per_mtok_in: float = 1.0
    usd_per_mtok_out: float = 4.0
    prefill_tok_per_s: float = 5000.0  # input processing speed
    decode_tok_per_s: float = 50.0
    answer_tokens: int = 30
    window_tokens: int = 128_000
    decay_per_doubling: float = (
        0.0  # reader accuracy lost each time context doubles beyond 1k tokens
    )
    floor: float = 0.3


def reliability(ctx_tokens: int, a: Assumptions) -> float:
    if ctx_tokens <= 1000:
        return 1.0
    return max(a.floor, 1.0 - a.decay_per_doubling * math.log2(ctx_tokens / 1000))


def cost_usd(ctx_tokens: int, a: Assumptions) -> float:
    return (ctx_tokens * a.usd_per_mtok_in + a.answer_tokens * a.usd_per_mtok_out) / 1e6


def latency_s(ctx_tokens: int, a: Assumptions) -> float:
    return ctx_tokens / a.prefill_tok_per_s + a.answer_tokens / a.decode_tok_per_s


class Setup:
    def __init__(self, n_systems: int = 24, queries_per_kind: int = 40, seed: int = 7):
        self.kb = generate_knowledge_base(
            n_systems=n_systems, queries_per_kind=queries_per_kind, seed=seed
        )
        ids = [d.doc_id for d in self.kb.docs]
        texts = [d.indexed_text for d in self.kb.docs]
        self.tokens = {d.doc_id: estimate_tokens(d.indexed_text) for d in self.kb.docs}
        bm = BM25Retriever(ids, texts)
        self.hybrid = HybridRetriever(bm, DenseRetriever(ids, texts, LSAEmbedder(dims=48)))
        self.order = ids

    def total_tokens(self) -> int:
        return sum(self.tokens.values())

    def long_context(self, qa, window: int) -> tuple[set[str], int]:
        """Stuff documents in corpus order until the window is full (the rest is truncated)."""
        used, ctx = 0, set()
        for d in self.order:
            if used + self.tokens[d] > window:
                break
            ctx.add(d)
            used += self.tokens[d]
        return ctx, used

    def rag(self, qa, k: int) -> tuple[set[str], int, float]:
        t0 = time.perf_counter()
        hits = [d for d, _ in self.hybrid.search(qa.question, k)]
        dt = time.perf_counter() - t0
        return set(hits), sum(self.tokens[d] for d in hits), dt


def evaluate(s: Setup, a: Assumptions, k: int = 3) -> dict:
    qa = s.kb.qa
    lc_ctx, lc_tok = s.long_context(qa, a.window_tokens)
    out = {"n_docs": len(s.kb.docs), "corpus_tokens": s.total_tokens(), "k": k}
    lc_hit = sum(q.gold_doc_id in lc_ctx for q in qa) / len(qa)
    rag_runs = [s.rag(q, k) for q in qa]
    rag_hit = sum(q.gold_doc_id in c for q, (c, _, _) in zip(qa, rag_runs, strict=True)) / len(qa)
    rag_tok = sum(t for _, t, _ in rag_runs) / len(qa)
    by_kind = {}
    for kind in sorted({q.kind for q in qa}):
        idx = [i for i, q in enumerate(qa) if q.kind == kind]
        by_kind[kind] = round(sum(qa[i].gold_doc_id in rag_runs[i][0] for i in idx) / len(idx), 3)
    out["long_context"] = {
        "context_tokens": lc_tok, "gold_in_context": round(lc_hit, 3),
        "est_accuracy": round(lc_hit * reliability(lc_tok, a), 3),
        "usd_per_query": round(cost_usd(lc_tok, a), 6), "latency_s": round(latency_s(lc_tok, a), 2),
    }  # fmt: skip
    out["rag"] = {
        "context_tokens": round(rag_tok, 1), "gold_in_context": round(rag_hit, 3),
        "est_accuracy": round(rag_hit * reliability(int(rag_tok), a), 3),
        "usd_per_query": round(cost_usd(int(rag_tok), a), 6),
        "latency_s": round(latency_s(int(rag_tok), a) + sum(d for _, _, d in rag_runs) / len(qa), 3),
        "retrieval_ms": round(1000 * sum(d for _, _, d in rag_runs) / len(qa), 2), "recall_by_kind": by_kind,
    }  # fmt: skip
    return out


def scale_projection(
    tokens_per_doc: float,
    rag_ctx_tokens: float,
    a: Assumptions,
    sizes=(100, 1_000, 10_000, 100_000),
) -> list[dict]:
    """Analytic extrapolation of context size, cost and feasibility. Retrieval quality at scale is NOT extrapolated."""
    rows = []
    for n in sizes:
        tok = int(n * tokens_per_doc)
        rows.append({
            "docs": n, "corpus_tokens": tok, "fits_window": tok <= a.window_tokens,
            "long_ctx_usd_per_query": round(cost_usd(tok, a), 4) if tok <= a.window_tokens else None,
            "rag_usd_per_query": round(cost_usd(int(rag_ctx_tokens), a), 6),
            "cost_ratio": round(cost_usd(tok, a) / cost_usd(int(rag_ctx_tokens), a), 1) if tok <= a.window_tokens else None,
        })  # fmt: skip
    return rows
