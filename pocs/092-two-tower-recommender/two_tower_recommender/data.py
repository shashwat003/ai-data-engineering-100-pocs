"""Synthetic implicit-feedback data with latent topics, popularity skew, time order and cold-start items.

Each user has a topic mixture; each item has a topic, a price bucket and a popularity weight (long tail). Interactions are
sampled from affinity x popularity. The last `n_test` interactions of every user are held out (temporal split); items first
seen after the training cut-off are 'cold' (never in training). History features only ever use earlier interactions.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Dataset:
    n_users: int
    n_items: int
    user_segment: np.ndarray  # (U,)
    item_topic: np.ndarray  # (I,)
    item_price: np.ndarray  # (I,)
    train: list[list[int]]  # per user, chronological item ids
    test: list[list[int]]  # per user, held-out items (chronological)
    cold_items: set[int]
    pop: np.ndarray  # training interaction counts per item


def generate(
    n_users: int = 1500,
    n_items: int = 800,
    n_topics: int = 12,
    seed: int = 0,
    per_user: tuple[int, int] = (14, 40),
    n_test: int = 2,
    cold_frac: float = 0.05,
) -> Dataset:
    rng = np.random.default_rng(seed)
    item_topic = rng.integers(0, n_topics, n_items)
    item_price = rng.integers(0, 4, n_items)
    pop_w = (np.arange(1, n_items + 1) ** -0.8)[rng.permutation(n_items)]
    pop_w /= pop_w.sum()
    cold = set(rng.choice(n_items, int(cold_frac * n_items), replace=False).tolist())
    user_seg = rng.integers(0, 4, n_users)
    train, test = [], []
    for _ in range(n_users):
        mix = rng.dirichlet(np.full(n_topics, 0.15))
        affinity = mix[item_topic] * 0.85 + 0.15 / n_topics
        p = affinity * pop_w
        n = int(rng.integers(*per_user))
        seq = []
        cold_p = p.copy()
        seen_ok = np.array([i not in cold for i in range(n_items)])
        while len(seq) < n:
            i = int(
                rng.choice(
                    n_items,
                    p=(p if len(seq) >= n - n_test else cold_p * seen_ok)
                    / (p if len(seq) >= n - n_test else cold_p * seen_ok).sum(),
                )
            )
            if i not in seq:
                seq.append(i)
        train.append(seq[: n - n_test])
        test.append(seq[n - n_test :])
    pop = np.zeros(n_items)
    for seq in train:
        np.add.at(pop, seq, 1)
    return Dataset(n_users, n_items, user_seg, item_topic, item_price, train, test, cold, pop)


def training_pairs(
    ds: Dataset, history: int = 10, rng: np.random.Generator | None = None
) -> list[tuple[int, list[int], int]]:
    """(user, previous items, target item) for every training interaction; the history only contains items BEFORE the target."""
    out = []
    for u, seq in enumerate(ds.train):
        for t in range(1, len(seq)):
            out.append((u, seq[max(0, t - history) : t], seq[t]))
    return out


def validation_split(ds: Dataset) -> Dataset:
    """Same data with each user's LAST training interaction moved to the test slot: used only for model selection, never for the final test."""
    train = [seq[:-1] for seq in ds.train]
    test = [seq[-1:] for seq in ds.train]
    pop = np.zeros(ds.n_items)
    for seq in train:
        np.add.at(pop, seq, 1)
    return Dataset(
        ds.n_users,
        ds.n_items,
        ds.user_segment,
        ds.item_topic,
        ds.item_price,
        train,
        test,
        set(),
        pop,
    )
