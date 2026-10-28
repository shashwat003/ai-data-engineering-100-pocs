"""Two-tower retrieval model in NumPy with hand-written backpropagation (PyTorch is not used).

user tower: [mean(item-id embeddings of history), user-segment embedding] -> ReLU(W1) -> W2 -> L2-normalise
item tower: [item-id embedding, price-bucket embedding, topic embedding (content feature, helps cold items)] -> ReLU(W1) -> W2 -> L2-normalise
loss: in-batch sampled softmax over cosine/temperature with logQ correction for popularity-biased negatives.
"""

from __future__ import annotations

import numpy as np

from .data import Dataset, training_pairs


class Adam:
    def __init__(self, lr=0.01, b1=0.9, b2=0.999, eps=1e-8):
        self.lr, self.b1, self.b2, self.eps, self.t = lr, b1, b2, eps, 0
        self.m: dict[int, np.ndarray] = {}
        self.v: dict[int, np.ndarray] = {}

    def step(self, params: list[np.ndarray], grads: list[np.ndarray]) -> None:
        self.t += 1
        for p, g in zip(params, grads, strict=True):
            k = id(p)
            m = self.m.setdefault(k, np.zeros_like(p))
            v = self.v.setdefault(k, np.zeros_like(p))
            m *= self.b1
            m += (1 - self.b1) * g
            v *= self.b2
            v += (1 - self.b2) * g * g
            p -= (
                self.lr
                * (m / (1 - self.b1**self.t))
                / (np.sqrt(v / (1 - self.b2**self.t)) + self.eps)
            )


def _normalise(z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = np.sqrt((z * z).sum(1, keepdims=True) + 1e-12)
    return z / n, n


def _norm_backward(e: np.ndarray, n: np.ndarray, de: np.ndarray) -> np.ndarray:
    return (de - e * (e * de).sum(1, keepdims=True)) / n


class TwoTower:
    def __init__(
        self, ds: Dataset, dim: int = 32, hidden: int = 64, temperature: float = 0.1, seed: int = 0
    ):
        rng = np.random.default_rng(seed)
        s = lambda *shape: rng.normal(0, 0.1, shape)  # noqa: E731
        self.ds, self.dim, self.tau = ds, dim, temperature
        self.item_emb, self.hist_emb = s(ds.n_items, dim), s(ds.n_items, dim)
        self.seg_emb, self.price_emb = s(4, dim), s(4, dim)
        self.topic_emb = s(int(ds.item_topic.max()) + 1, dim)
        self.U_W1, self.U_b1, self.U_W2, self.U_b2 = (
            s(2 * dim, hidden),
            np.zeros(hidden),
            s(hidden, dim),
            np.zeros(dim),
        )
        self.I_W1, self.I_b1, self.I_W2, self.I_b2 = (
            s(3 * dim, hidden),
            np.zeros(hidden),
            s(hidden, dim),
            np.zeros(dim),
        )
        self.params = [
            self.item_emb,
            self.hist_emb,
            self.seg_emb,
            self.price_emb,
            self.topic_emb,
            self.U_W1,
            self.U_b1,
            self.U_W2,
            self.U_b2,
            self.I_W1,
            self.I_b1,
            self.I_W2,
            self.I_b2,
        ]

    # --- forward ---------------------------------------------------------------------------------
    def user_forward(self, users: np.ndarray, hists: list[list[int]]):
        pooled = np.zeros((len(users), self.dim))
        counts = np.array([max(1, len(h)) for h in hists], dtype=float)[:, None]
        for r, h in enumerate(hists):
            if h:
                pooled[r] = self.hist_emb[h].sum(0)
        pooled /= counts
        x = np.concatenate([pooled, self.seg_emb[self.ds.user_segment[users]]], 1)
        a = x @ self.U_W1 + self.U_b1
        h = np.maximum(a, 0)
        z = h @ self.U_W2 + self.U_b2
        e, n = _normalise(z)
        return e, (users, hists, counts, x, a, h, e, n)

    def item_forward(self, items: np.ndarray):
        x = np.concatenate(
            [
                self.item_emb[items],
                self.price_emb[self.ds.item_price[items]],
                self.topic_emb[self.ds.item_topic[items]],
            ],
            1,
        )
        a = x @ self.I_W1 + self.I_b1
        h = np.maximum(a, 0)
        z = h @ self.I_W2 + self.I_b2
        e, n = _normalise(z)
        return e, (items, x, a, h, e, n)

    # --- loss + backward ---------------------------------------------------------------------------
    def loss_and_grads(self, users, hists, items, log_q):
        eu, cu = self.user_forward(users, hists)
        ei, ci = self.item_forward(items)
        logits = eu @ ei.T / self.tau - log_q[None, :]
        dup = (items[:, None] == items[None, :]) & ~np.eye(len(items), dtype=bool)
        logits[dup] = -1e9  # the same item twice in a batch is not a negative for itself
        logits -= logits.max(1, keepdims=True)
        p = np.exp(logits)
        p /= p.sum(1, keepdims=True)
        b = len(users)
        loss = -np.log(p[np.arange(b), np.arange(b)] + 1e-12).mean()
        dl = p.copy()
        dl[np.arange(b), np.arange(b)] -= 1
        dl /= b
        deu, dei = dl @ ei / self.tau, dl.T @ eu / self.tau
        g = {id(q): np.zeros_like(q) for q in self.params}

        def back_tower(de, cache, W1, b1, W2, b2):
            x, a, h, e, n = cache[-5:]
            dz = _norm_backward(e, n, de)
            g[id(W2)] += h.T @ dz
            g[id(b2)] += dz.sum(0)
            da = (dz @ W2.T) * (a > 0)
            g[id(W1)] += x.T @ da
            g[id(b1)] += da.sum(0)
            return da @ W1.T

        dxu = back_tower(deu, cu, self.U_W1, self.U_b1, self.U_W2, self.U_b2)
        users_, hists_, counts, *_ = cu
        np.add.at(g[id(self.seg_emb)], self.ds.user_segment[users_], dxu[:, self.dim :])
        for r, hs in enumerate(hists_):
            if hs:
                np.add.at(
                    g[id(self.hist_emb)],
                    hs,
                    np.repeat((dxu[r, : self.dim] / counts[r])[None, :], len(hs), 0),
                )
        dxi = back_tower(dei, ci, self.I_W1, self.I_b1, self.I_W2, self.I_b2)
        np.add.at(g[id(self.item_emb)], items, dxi[:, : self.dim])
        np.add.at(g[id(self.price_emb)], self.ds.item_price[items], dxi[:, self.dim : 2 * self.dim])
        np.add.at(g[id(self.topic_emb)], self.ds.item_topic[items], dxi[:, 2 * self.dim :])
        return float(loss), [g[id(q)] for q in self.params]

    # --- inference ---------------------------------------------------------------------------------
    def all_item_vectors(self) -> np.ndarray:
        return self.item_forward(np.arange(self.ds.n_items))[0].astype("float32")

    def user_vectors(self, users: np.ndarray, hists: list[list[int]]) -> np.ndarray:
        return self.user_forward(users, hists)[0].astype("float32")


def train(
    model: TwoTower,
    epochs: int = 12,
    batch: int = 256,
    lr: float = 0.01,
    history: int = 10,
    seed: int = 0,
    log=None,
) -> list[float]:
    pairs = training_pairs(model.ds, history)
    rng = np.random.default_rng(seed)
    q = (model.ds.pop + 1) / (
        model.ds.pop + 1
    ).sum()  # sampling probability of an item as an in-batch negative
    log_q_all = np.log(q)
    opt, losses = Adam(lr), []
    for ep in range(epochs):
        order = rng.permutation(len(pairs))
        tot, nb = 0.0, 0
        for s in range(0, len(order) - batch + 1, batch):
            idx = order[s : s + batch]
            users = np.array([pairs[i][0] for i in idx])
            hists = [pairs[i][1] for i in idx]
            items = np.array([pairs[i][2] for i in idx])
            loss, grads = model.loss_and_grads(users, hists, items, log_q_all[items])
            opt.step(model.params, grads)
            tot, nb = tot + loss, nb + 1
        losses.append(tot / max(nb, 1))
        if log:
            log(ep, losses[-1])
    return losses
