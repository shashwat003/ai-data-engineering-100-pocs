"""Dense retrieval without model downloads: LSA (TF-IDF + truncated SVD) over words and char trigrams.

This is a classical dense embedding, not a neural one. It captures corpus co-occurrence and is
robust to small typos via character n-grams. Neural alternatives live in embedders.py.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

import numpy as np

from .embedders import Embedder, normalise_rows
from .tokenize import char_ngrams, tokenize


class LSAEmbedder:
    def __init__(self, dims: int = 64, ngram: int = 3):
        self.dims, self.ngram = dims, ngram

    def _features(self, text: str) -> Counter[str]:
        tokens = tokenize(text)
        feats = list(tokens)
        for tok in tokens:
            feats.extend("#c:" + g for g in char_ngrams(tok, self.ngram))
        return Counter(feats)

    def _tfidf(self, texts: Sequence[str]) -> np.ndarray:
        x = np.zeros((len(texts), len(self._vocab)), dtype=np.float64)
        for row, text in enumerate(texts):
            for feat, tf in self._features(text).items():
                col = self._vocab.get(feat)
                if col is not None:
                    x[row, col] = (1.0 + math.log(tf)) * self._idf[col]
        return normalise_rows(x)

    def fit(self, texts: Sequence[str]) -> None:
        feature_sets = [self._features(t) for t in texts]
        df = Counter(f for fs in feature_sets for f in fs)
        self._vocab = {f: i for i, f in enumerate(sorted(df))}
        n = len(texts)
        self._idf = np.array([math.log((1 + n) / (1 + df[f])) + 1.0 for f in self._vocab])
        x = self._tfidf(texts)
        _, _, vt = np.linalg.svd(x, full_matrices=False)
        self._components = vt[: min(self.dims, vt.shape[0])]

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        return normalise_rows(self._tfidf(texts) @ self._components.T)


class DenseIndex:
    def __init__(self, embedder: Embedder):
        self.embedder = embedder
        self.doc_ids: list[str] = []

    def fit(self, doc_ids: Sequence[str], texts: Sequence[str]) -> DenseIndex:
        self.doc_ids = list(doc_ids)
        self.embedder.fit(texts)
        self._matrix = self.embedder.encode(texts)
        return self

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        encode_q = getattr(self.embedder, "encode_queries", self.embedder.encode)
        sims = self._matrix @ encode_q([query])[0]
        order = np.lexsort((np.arange(len(sims)), -sims))[:k]  # score desc, index asc
        return [(self.doc_ids[i], float(sims[i])) for i in order]
