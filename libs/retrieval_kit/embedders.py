"""Embedder protocol plus optional neural embedders (local, free, lazily imported).

- SentenceTransformerEmbedder: Hugging Face sentence-transformers model on CPU/GPU. Needs
  `pip install -r requirements-neural.txt` and a one-time model download.
- OllamaEmbedder: any embedding model served by a local Ollama server (`ollama pull <model>`).

Neither runs in the default demo or in CI. Both are covered by unit tests using fakes only.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    def fit(self, texts: Sequence[str]) -> None: ...
    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


def normalise_rows(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return np.divide(m, norms, out=np.zeros_like(m), where=norms > 0)


class CachedEmbedder:
    """Memoise per text so the same query is embedded once across retrievers and ablations."""

    def __init__(self, inner):
        self.inner = inner
        self._cache: dict = {}
        self.calls = 0  # texts actually sent to the inner embedder

    def fit(self, texts: Sequence[str]) -> None:
        self.inner.fit(texts)

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if missing:
            self.calls += len(missing)
            for text, vec in zip(missing, self.inner.encode(missing), strict=True):
                self._cache[text] = vec
        return np.stack([self._cache[t] for t in texts])

    def encode_queries(self, texts: Sequence[str]) -> np.ndarray:
        inner_q = getattr(self.inner, "encode_queries", None)
        if inner_q is None:
            return self.encode(texts)
        key = [("q", t) for t in texts]
        missing = [t for (_, t) in dict.fromkeys(key) if ("q", t) not in self._cache]
        if missing:
            self.calls += len(missing)
            for text, vec in zip(missing, inner_q(missing), strict=True):
                self._cache[("q", text)] = vec
        return np.stack([self._cache[k] for k in key])

    def describe(self) -> dict:
        return getattr(self.inner, "describe", lambda: {})()


class OllamaEmbedder:
    """Neural embeddings from a local Ollama server (e.g. `ollama pull nomic-embed-text`)."""

    def __init__(self, client, model: str = "nomic-embed-text"):
        self.client, self.model = client, model

    def fit(self, texts: Sequence[str]) -> None:  # nothing to learn
        return None

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        vectors = self.client.embed(list(texts), model=self.model)
        return normalise_rows(np.array(vectors, dtype=np.float64))

    def describe(self) -> dict:
        return {"kind": "ollama", "model": self.model}


class SentenceTransformerEmbedder:
    """Local sentence-transformers model. `model` may be injected (tests); otherwise it is loaded.

    BGE-style retrieval models expect an instruction prefix on queries (not documents); pass
    `query_prefix` for those. Models without such a convention use the default empty prefix.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        *,
        query_prefix: str = "",
        batch_size: int = 32,
        model=None,
    ):
        self.model_name, self.query_prefix, self.batch_size = model_name, query_prefix, batch_size
        self._model = model

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise ImportError(
                    "sentence-transformers is not installed. Run "
                    "`pip install -r requirements-neural.txt` (large download: PyTorch) "
                    "or use --neural ollama."
                ) from exc
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def fit(self, texts: Sequence[str]) -> None:
        self._load()

    def _encode(self, texts: Sequence[str]) -> np.ndarray:
        vectors = self._load().encode(
            list(texts), batch_size=self.batch_size, show_progress_bar=False
        )
        return normalise_rows(np.asarray(vectors, dtype=np.float64))

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        return self._encode(texts)

    def encode_queries(self, texts: Sequence[str]) -> np.ndarray:
        return self._encode([self.query_prefix + t for t in texts])

    def describe(self) -> dict:
        info = {"kind": "sentence-transformers", "model": self.model_name}
        try:
            import sentence_transformers

            info["library_version"] = sentence_transformers.__version__
        except ImportError:
            pass
        return info
