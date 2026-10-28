"""Teacher (a soft-voting ensemble) and a small softmax-regression student trained on soft labels with a hand-written KL loss."""

from __future__ import annotations

import numpy as np
from scipy import sparse
from sklearn.decomposition import TruncatedSVD
from sklearn.ensemble import HistGradientBoostingClassifier, VotingClassifier
from sklearn.feature_extraction.text import HashingVectorizer, TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import make_pipeline

from .data import CATEGORIES


def make_teacher(seed: int = 0) -> VotingClassifier:
    """A deliberately heavier model than the student: n-gram TF-IDF logistic regression + naive Bayes + SVD/gradient boosting."""
    tfidf = lambda: TfidfVectorizer(ngram_range=(1, 3), sublinear_tf=True, min_df=1)  # noqa: E731
    return VotingClassifier(
        [
            ("lr", make_pipeline(tfidf(), LogisticRegression(C=20, max_iter=2000))),
            ("nb", make_pipeline(tfidf(), MultinomialNB(alpha=0.05))),
            (
                "gb",
                make_pipeline(
                    tfidf(),
                    TruncatedSVD(40, random_state=seed),
                    HistGradientBoostingClassifier(max_iter=120, random_state=seed),
                ),
            ),
        ],
        voting="soft",
    )


def softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def temper(p: np.ndarray, t: float) -> np.ndarray:
    """Soften (t > 1) or sharpen (t < 1) a probability matrix: p^(1/t), renormalised."""
    q = np.power(np.clip(p, 1e-9, 1), 1.0 / t)
    return q / q.sum(1, keepdims=True)


class SoftmaxStudent:
    """Multinomial logistic regression over hashed unigram features: tiny, fast, trained on target distributions."""

    def __init__(
        self,
        n_features: int = 2**11,
        l2: float = 1e-4,
        epochs: int = 60,
        lr: float = 0.5,
        seed: int = 0,
    ):
        self.vec = HashingVectorizer(
            n_features=n_features, ngram_range=(1, 1), alternate_sign=False, norm="l2"
        )
        self.l2, self.epochs, self.lr, self.seed = l2, epochs, lr, seed
        self.W = np.zeros((n_features, len(CATEGORIES)), dtype=np.float64)
        self.b = np.zeros(len(CATEGORIES))

    def featurise(self, texts: list[str]) -> sparse.csr_matrix:
        return self.vec.transform(texts)

    def fit(self, texts: list[str], targets: np.ndarray) -> SoftmaxStudent:
        """targets: (n, classes) rows summing to 1 (one-hot for hard labels, teacher probabilities for distillation). Minimises KL(target || student)."""
        x = self.featurise(texts)
        n = x.shape[0]
        rng = np.random.default_rng(self.seed)
        mW, vW, mb, vb = (
            np.zeros_like(self.W),
            np.zeros_like(self.W),
            np.zeros_like(self.b),
            np.zeros_like(self.b),
        )
        t = 0
        for _ in range(self.epochs):
            order = rng.permutation(n)
            for s in range(0, n, 128):
                idx = order[s : s + 128]
                xb, tb = x[idx], targets[idx]
                p = softmax(xb @ self.W + self.b)
                g = (p - tb) / len(idx)
                gW = np.asarray(xb.T @ g) + self.l2 * self.W
                gb = g.sum(0)
                t += 1
                for param, grad, m, v in ((self.W, gW, mW, vW), (self.b, gb, mb, vb)):
                    m *= 0.9
                    m += 0.1 * grad
                    v *= 0.999
                    v += 0.001 * grad * grad
                    param -= (
                        self.lr * 0.1 * (m / (1 - 0.9**t)) / (np.sqrt(v / (1 - 0.999**t)) + 1e-8)
                    )
        return self

    def predict_proba(self, texts: list[str]) -> np.ndarray:
        return softmax(self.featurise(texts) @ self.W + self.b)

    @property
    def n_params(self) -> int:
        return int(self.W.size + self.b.size)


def one_hot(labels: list[str]) -> np.ndarray:
    idx = np.array([CATEGORIES.index(c) for c in labels])
    out = np.zeros((len(labels), len(CATEGORIES)))
    out[np.arange(len(labels)), idx] = 1
    return out


def ece(p: np.ndarray, y_idx: np.ndarray, bins: int = 10) -> float:
    """Expected calibration error of the top-1 confidence."""
    conf, pred = p.max(1), p.argmax(1)
    err = 0.0
    for lo in np.linspace(0, 1, bins, endpoint=False):
        m = (conf > lo) & (conf <= lo + 1 / bins)
        if m.any():
            err += m.mean() * abs((pred[m] == y_idx[m]).mean() - conf[m].mean())
    return float(err)
