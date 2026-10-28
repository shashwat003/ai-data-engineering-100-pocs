"""Idempotent document -> chunk -> embedding ingestion into a SQLite-backed vector store.

Chunk identity is content-addressed: key = sha256(embedding model version, chunk text). Unchanged
chunks are never re-embedded; edited documents re-embed only the chunks that changed; removed text
is deleted from the index; re-running any ingestion is a no-op. Embedding calls are counted because
that is the real cost driver.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable

import numpy as np

DIM = 256
_TOKEN = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    """Deterministic, dependency-free embedder (hashing trick over unigrams + bigrams), L2-normalised.
    A stand-in for a real model: what matters here is the call count and the versioning."""

    def __init__(self, version: str = "hash-v1", dim: int = DIM):
        self.version, self.dim, self.calls = version, dim, 0

    def embed(self, texts: list[str]) -> np.ndarray:
        self.calls += len(texts)
        out = np.zeros((len(texts), self.dim), np.float32)
        for i, t in enumerate(texts):
            toks = _TOKEN.findall(t.lower())
            for f in toks + [a + "_" + b for a, b in zip(toks, toks[1:], strict=False)]:
                h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
                out[i, h % self.dim] += 1.0 if (h >> 63) else -1.0
            n = np.linalg.norm(out[i])
            out[i] /= n or 1.0
        return out


def chunk(text: str, size: int = 40, overlap: int = 0) -> list[str]:
    """Split on words into chunks of `size` words (paragraph-stable for the demo corpus)."""
    words = text.split()
    step = max(1, size - overlap)
    return [
        " ".join(words[i : i + size])
        for i in range(0, max(len(words), 1), step)
        if words[i : i + size]
    ]


def chunk_key(version: str, text: str) -> str:
    return hashlib.sha256(f"{version}\x00{text}".encode()).hexdigest()


class VectorStore:
    def __init__(
        self,
        db: sqlite3.Connection,
        embedder: HashingEmbedder,
        chunker: Callable[[str], list[str]] = chunk,
    ):
        self.db, self.emb, self.chunker = db, embedder, chunker
        db.executescript("""
        CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, text TEXT NOT NULL, vec BLOB NOT NULL, version TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS doc_chunks (doc_id TEXT, pos INTEGER, key TEXT NOT NULL, PRIMARY KEY (doc_id, pos));
        CREATE TABLE IF NOT EXISTS docs (doc_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL);""")

    def upsert(self, doc_id: str, text: str) -> dict:
        h = hashlib.sha256(text.encode()).hexdigest()
        row = self.db.execute("SELECT content_hash FROM docs WHERE doc_id=?", (doc_id,)).fetchone()
        have_all = (
            row
            and row[0] == h
            and self.db.execute(
                "SELECT COUNT(*) FROM doc_chunks d JOIN vectors v ON v.key=d.key AND v.version=? WHERE d.doc_id=?",
                (self.emb.version, doc_id),
            ).fetchone()[0]
            == self.db.execute(
                "SELECT COUNT(*) FROM doc_chunks WHERE doc_id=?", (doc_id,)
            ).fetchone()[0]
        )
        if have_all:
            return {"doc": doc_id, "status": "unchanged", "embedded": 0}
        chunks = self.chunker(text)
        keys = [chunk_key(self.emb.version, c) for c in chunks]
        known = (
            {
                k
                for (k,) in self.db.execute(
                    f"SELECT key FROM vectors WHERE key IN ({','.join('?' * len(keys))})", keys
                )
            }
            if keys
            else set()
        )
        todo = [(k, c) for k, c in dict(zip(keys, chunks, strict=True)).items() if k not in known]
        vecs = (
            self.emb.embed([c for _, c in todo])
            if todo
            else np.zeros((0, self.emb.dim), np.float32)
        )
        with self.db:  # all-or-nothing: a crash cannot leave a doc half-indexed
            for (k, c), v in zip(todo, vecs, strict=True):
                self.db.execute(
                    "INSERT OR IGNORE INTO vectors VALUES (?,?,?,?)",
                    (k, c, v.tobytes(), self.emb.version),
                )
            self.db.execute("DELETE FROM doc_chunks WHERE doc_id=?", (doc_id,))
            self.db.executemany(
                "INSERT INTO doc_chunks VALUES (?,?,?)",
                [(doc_id, i, k) for i, k in enumerate(keys)],
            )
            self.db.execute("INSERT OR REPLACE INTO docs VALUES (?,?)", (doc_id, h))
            self._gc()
        return {"doc": doc_id, "status": "updated" if row else "new", "embedded": len(todo)}

    def delete(self, doc_id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM doc_chunks WHERE doc_id=?", (doc_id,))
            self.db.execute("DELETE FROM docs WHERE doc_id=?", (doc_id,))
            self._gc()

    def _gc(self) -> None:
        """Drop vectors no document references any more (shared chunks survive while any doc uses them)."""
        self.db.execute("DELETE FROM vectors WHERE key NOT IN (SELECT key FROM doc_chunks)")

    def sync(self, corpus: dict[str, str]) -> dict:
        """Make the store equal to `corpus` (doc_id -> text): upsert everything, delete what vanished."""
        before = self.emb.calls
        results = [self.upsert(d, t) for d, t in sorted(corpus.items())]
        gone = [d for (d,) in self.db.execute("SELECT doc_id FROM docs") if d not in corpus]
        for d in gone:
            self.delete(d)
        return {"embedded": self.emb.calls - before, "new": sum(r["status"] == "new" for r in results),
                "updated": sum(r["status"] == "updated" for r in results),
                "unchanged": sum(r["status"] == "unchanged" for r in results), "deleted": len(gone)}  # fmt: skip

    def search(self, query: str, k: int = 3) -> list[tuple[str, int, float]]:
        rows = self.db.execute(
            "SELECT d.doc_id, d.pos, v.vec FROM doc_chunks d JOIN vectors v ON v.key=d.key"
        ).fetchall()
        if not rows:
            return []
        mat = np.frombuffer(b"".join(r[2] for r in rows), np.float32).reshape(len(rows), -1)
        q = self.emb.embed([query])[0]
        self.emb.calls -= 1  # queries are not ingestion cost
        sims = mat @ q
        order = sorted(range(len(rows)), key=lambda i: (-sims[i], rows[i][0], rows[i][1]))[:k]
        return [(rows[i][0], rows[i][1], round(float(sims[i]), 4)) for i in order]

    def stats(self) -> dict:
        q = lambda s: self.db.execute(s).fetchone()[0]  # noqa: E731
        return {
            "docs": q("SELECT COUNT(*) FROM docs"),
            "chunk_refs": q("SELECT COUNT(*) FROM doc_chunks"),
            "vectors": q("SELECT COUNT(*) FROM vectors"),
        }
