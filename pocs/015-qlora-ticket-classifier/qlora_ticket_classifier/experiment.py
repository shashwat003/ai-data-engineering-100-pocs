"""Pretrain a small encoder on a COARSE ticket task, then adapt it to the FINE 6-class task with few labels."""

from __future__ import annotations

import copy

import numpy as np
from llm_to_slm_distillation.data import CATEGORIES, generate
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score

from .lora_net import LoraNet, featurize, fit

COARSE = {
    "billing": 0,
    "cancellation": 0,
    "login": 1,
    "bug": 1,
    "shipping": 2,
    "feature_request": 2,
}
FINE = {c: i for i, c in enumerate(CATEGORIES)}
SIZES = [1024, 256, 128]
RANK, ALPHA = 8, 16.0


def pretrain(seed: int = 0, n: int = 6000, epochs: int = 15) -> tuple[LoraNet, float]:
    texts, labels = generate(n, seed + 1)
    x, y = featurize(texts), np.array([COARSE[c] for c in labels])
    net = LoraNet(SIZES, 3, seed)
    net.mode = "full"
    fit(net, x, y, epochs=epochs, lr=3e-3, seed=seed)
    return net, float((net.predict(x) == y).mean())


def fine_data(n: int, seed: int) -> tuple[list[str], np.ndarray]:
    texts, labels = generate(n, seed)
    return texts, np.array([FINE[c] for c in labels])


def adapt(
    base: LoraNet,
    mode: str,
    texts: list[str],
    y: np.ndarray,
    seed: int,
    *,
    rank: int = RANK,
    epochs: int = 40,
) -> LoraNet:
    m = copy.deepcopy(base)
    m.reset_head(len(CATEGORIES), seed)
    if mode == "qlora":
        m.quantise_base("Q4_0")
    if mode in ("lora", "qlora"):
        m.add_lora(rank, ALPHA, seed)
    m.mode = mode
    fit(m, featurize(texts), y, epochs=epochs, lr=2e-3 if mode == "full" else 5e-3, seed=seed)
    return m


def score(pred: np.ndarray, y: np.ndarray) -> dict:
    return {"acc": float((pred == y).mean()), "macro_f1": float(f1_score(y, pred, average="macro"))}


def tfidf_baseline(texts: list[str], y: np.ndarray, test_texts: list[str]) -> np.ndarray:
    vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)
    return (
        LogisticRegression(max_iter=1000, C=10)
        .fit(vec.fit_transform(texts), y)
        .predict(vec.transform(test_texts))
    )
