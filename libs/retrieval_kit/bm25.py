from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Sequence

from .tokenize import tokenize


class BM25Index:
    """Okapi BM25 over an in-memory inverted index (no stemming; lowercase alphanumerics)."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.doc_ids: list[str] = []

    def fit(self, doc_ids: Sequence[str], texts: Sequence[str]) -> BM25Index:
        self.doc_ids = list(doc_ids)
        self._postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self._doc_len: list[int] = []
        for idx, text in enumerate(texts):
            tokens = tokenize(text)
            self._doc_len.append(len(tokens))
            for term, tf in Counter(tokens).items():
                self._postings[term].append((idx, tf))
        n = len(self.doc_ids)
        self._avgdl = (sum(self._doc_len) / n) or 1.0
        self._idf = {
            t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self._postings.items()
        }
        return self

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        scores: dict[int, float] = defaultdict(float)
        for term in set(tokenize(query)):
            for idx, tf in self._postings.get(term, ()):
                norm = 1 - self.b + self.b * self._doc_len[idx] / self._avgdl
                scores[idx] += self._idf[term] * tf * (self.k1 + 1) / (tf + self.k1 * norm)
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
        return [(self.doc_ids[i], s) for i, s in ranked]
