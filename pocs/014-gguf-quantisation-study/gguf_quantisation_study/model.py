"""A small character-level language model (scikit-learn MLP) trained on synthetic text; quantise its weights and measure perplexity."""

from __future__ import annotations

import copy
import warnings

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.neural_network import MLPClassifier

from .quant import bits_per_weight, dequantise, quantise

ALPHABET = "abcdefghijklmnopqrstuvwxyz ."
CTX = 6
WORDS = [
    "data",
    "model",
    "the",
    "and",
    "pipeline",
    "stream",
    "lake",
    "table",
    "train",
    "serve",
    "query",
    "index",
    "cache",
    "batch",
    "metric",
    "drift",
    "schema",
    "quality",
    "vector",
    "agent",
    "token",
    "latency",
    "cost",
    "audit",
    "policy",
]


def make_text(n_chars: int, seed: int) -> str:
    rng = np.random.default_rng(seed)
    p = 1 / np.arange(1, len(WORDS) + 1) ** 0.9
    p /= p.sum()
    out, order = [], rng.permutation(len(WORDS))
    prev = 0
    while sum(len(w) + 1 for w in out) < n_chars:
        # first-order word Markov chain with a fixed random transition bias, so context matters
        bias = np.roll(p, prev * 3 % len(WORDS))
        w = int(rng.choice(len(WORDS), p=bias / bias.sum()))
        out.append(WORDS[order[w]])
        prev = w
        if rng.random() < 0.12:
            out[-1] += "."
    return " ".join(out)[:n_chars]


def encode(text: str) -> tuple[np.ndarray, np.ndarray]:
    idx = np.array([ALPHABET.index(c) if c in ALPHABET else ALPHABET.index(" ") for c in text])
    n = len(idx) - CTX
    x = np.zeros((n, CTX * len(ALPHABET)), dtype=np.float32)
    for k in range(CTX):
        x[np.arange(n), k * len(ALPHABET) + idx[k : k + n]] = 1
    return x, idx[CTX:]


def train(
    seed: int = 0, n_train: int = 40_000, hidden: tuple[int, ...] = (256, 256, 128)
) -> tuple[MLPClassifier, tuple, tuple]:
    xtr, ytr = encode(make_text(n_train, seed))
    xte, yte = encode(make_text(10_000, seed + 100))
    m = MLPClassifier(
        hidden_layer_sizes=hidden,
        max_iter=30,
        random_state=seed,
        batch_size=256,
        learning_rate_init=0.003,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        m.fit(xtr, ytr)
    return m, (xtr, ytr), (xte, yte)


def evaluate(m: MLPClassifier, x: np.ndarray, y: np.ndarray) -> dict:
    p = m.predict_proba(x)
    cls = {c: i for i, c in enumerate(m.classes_)}
    pt = np.array([p[i, cls[t]] if t in cls else 1e-9 for i, t in enumerate(y)])
    ce = float(-np.log(np.clip(pt, 1e-9, None)).mean())
    return {
        "perplexity": round(float(np.exp(ce)), 4),
        "accuracy": round(float((m.classes_[p.argmax(1)] == y).mean()), 4),
    }


def quantised_copy(m: MLPClassifier, plan: list[str]) -> tuple[MLPClassifier, int, int]:
    """Quantise weight matrices (biases stay fp32): plan[i] is the format for layer i or 'F32'. Returns (model, bytes, n_weights)."""
    q = copy.deepcopy(m)
    nbytes = nweights = 0
    for i, (w, b) in enumerate(zip(m.coefs_, m.intercepts_, strict=True)):
        nweights += w.size
        if plan[i] == "F32":
            nbytes += w.nbytes
        else:
            p = quantise(w, plan[i])
            q.coefs_[i] = dequantise(p).astype(w.dtype)
            nbytes += p.nbytes
        nbytes += b.nbytes
    return q, nbytes, nweights


def model_size_gb(params: float, fmt: str) -> float:
    """MODELLED file size of a hypothetical model with `params` weights, ignoring embeddings/metadata/mixed formats."""
    return params * (32 if fmt == "F32" else 16 if fmt == "F16" else bits_per_weight(fmt)) / 8 / 1e9
