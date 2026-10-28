from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .bm25 import BM25Index
from .fusion import reciprocal_rank_fusion, weighted_fusion
from .lsa import DenseIndex


class Retriever(Protocol):
    name: str

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]: ...


class BM25Retriever:
    name = "bm25"

    def __init__(self, doc_ids: Sequence[str], texts: Sequence[str]):
        self.index = BM25Index().fit(doc_ids, texts)

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        return self.index.search(query, k)


class DenseRetriever:
    def __init__(self, doc_ids: Sequence[str], texts: Sequence[str], embedder, name: str = "dense"):
        self.name = name
        self.index = DenseIndex(embedder).fit(doc_ids, texts)

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        return self.index.search(query, k)


class HybridRetriever:
    """Fuse sparse and dense candidates. mode='rrf' (rank-based) or 'weighted' (alpha = dense weight)."""

    def __init__(
        self,
        sparse: Retriever,
        dense: Retriever,
        *,
        mode: str = "rrf",
        alpha: float = 0.5,
        depth: int = 50,
        name: str | None = None,
    ):
        if mode not in {"rrf", "weighted"}:
            raise ValueError("mode must be 'rrf' or 'weighted'")
        self.sparse, self.dense = sparse, dense
        self.mode, self.alpha, self.depth = mode, alpha, depth
        self.name = name or f"hybrid_{mode}"

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        s = self.sparse.search(query, self.depth)
        d = self.dense.search(query, self.depth)
        if self.mode == "rrf":
            fused = reciprocal_rank_fusion([[x for x, _ in s], [x for x, _ in d]])
        else:
            fused = weighted_fusion([s, d], [1 - self.alpha, self.alpha])
        return fused[:k]
