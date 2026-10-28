"""Small experiments with and without deliberate reproducibility flaws. Each returns a JSON-able result."""

from __future__ import annotations

import hashlib
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from .seed import set_all_seeds


def _data(n=400, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 6))
    y = (X[:, 0] + 0.5 * X[:, 1] * X[:, 2] + rng.normal(0, 0.5, n) > 0).astype(int)
    return X, y


def _sgd(X, y, rng, epochs=25, lr=0.1):
    """Tiny logistic-regression trainer in numpy: shuffling and initial weights come from `rng`."""
    w = rng.normal(0, 0.1, X.shape[1])
    for _ in range(epochs):
        for idx in np.array_split(rng.permutation(len(X)), 10):
            p = 1 / (1 + np.exp(-X[idx] @ w))
            w -= lr * X[idx].T @ (p - y[idx]) / len(idx)
    return w


def seeded(cfg):
    """Everything seeded explicitly: data, split, initial weights, shuffling."""
    set_all_seeds(cfg["seed"])
    X, y = _data(seed=cfg["seed"])
    rng = np.random.default_rng(cfg["seed"])
    perm = rng.permutation(len(X))
    tr, te = perm[:300], perm[300:]
    w = _sgd(X[tr], y[tr], rng)
    return {
        "acc": round(float((((X[te] @ w) > 0) == y[te]).mean()), 6),
        "w": [round(float(v), 6) for v in w],
    }


def unseeded(cfg):
    """FLAW: split, initial weights and shuffling use OS entropy."""
    X, y = _data(seed=cfg["seed"])
    rng = np.random.default_rng()
    perm = rng.permutation(len(X))
    tr, te = perm[:300], perm[300:]
    w = _sgd(X[tr], y[tr], rng)
    return {
        "acc": round(float((((X[te] @ w) > 0) == y[te]).mean()), 6),
        "w": [round(float(v), 6) for v in w],
    }


def hash_order(cfg):
    """FLAW: iterates a set of strings; the order depends on PYTHONHASHSEED, and a floating-point reduction depends on the order."""
    random.seed(cfg["seed"])
    items = {f"feature_{i}" for i in range(50)}
    vals = {k: random.random() * 10 ** random.randint(-8, 8) for k in sorted(items)}
    total = 0.0
    for k in items:  # unordered iteration
        total += vals[k]
    return {"sum": repr(total), "first_key": next(iter(items))}


def hash_order_fixed(cfg):
    random.seed(cfg["seed"])
    items = sorted({f"feature_{i}" for i in range(50)})
    vals = {k: random.random() * 10 ** random.randint(-8, 8) for k in items}
    total = 0.0
    for k in items:
        total += vals[k]
    return {"sum": repr(total), "first_key": items[0]}


def thread_race(cfg):
    """FLAW: float accumulation in completion order of worker threads."""
    rng = np.random.default_rng(cfg["seed"])
    xs = (rng.random(64) * 10.0 ** rng.integers(-6, 7, 64)).tolist()

    def work(x):
        time.sleep(random.random() * 0.002)
        return x

    total = 0.0
    with ThreadPoolExecutor(8) as ex:
        for f in as_completed([ex.submit(work, x) for x in xs]):
            total += f.result()
    return {"sum": repr(total)}


def thread_fixed(cfg):
    rng = np.random.default_rng(cfg["seed"])
    xs = (rng.random(64) * 10.0 ** rng.integers(-6, 7, 64)).tolist()
    with ThreadPoolExecutor(8) as ex:
        parts = list(ex.map(lambda x: x, xs))  # map preserves input order
    total = 0.0
    for p in parts:
        total += p
    return {"sum": repr(total)}


def clock_id(cfg):
    """FLAW: a run id derived from the wall clock leaks into the result."""
    return {"run_id": hashlib.sha1(str(time.time_ns()).encode()).hexdigest()[:8], "acc": 0.9}


def linear(cfg):
    from sklearn.linear_model import LogisticRegression

    set_all_seeds(cfg["seed"])
    X, y = _data(seed=cfg["seed"])
    m = LogisticRegression(max_iter=300).fit(X, y)
    return {"coef": [round(float(c), 8) for c in m.coef_[0]]}


EXPERIMENTS = {
    "seeded": seeded,
    "unseeded": unseeded,
    "hash_order": hash_order,
    "hash_order_fixed": hash_order_fixed,
    "thread_race": thread_race,
    "thread_fixed": thread_fixed,
    "clock_id": clock_id,
    "linear": linear,
}

if (
    __name__ == "__main__"
):  # child process entry: python -m reproducibility_kit.experiments <name> <json config>
    cfg = json.loads(sys.argv[2])
    print(json.dumps(EXPERIMENTS[sys.argv[1]](cfg), sort_keys=True))
