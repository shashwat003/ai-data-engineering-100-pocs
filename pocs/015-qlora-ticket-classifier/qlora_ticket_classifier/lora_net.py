"""A small MLP with LoRA adapters and an optional 4-bit blockwise-quantised frozen base, trained with hand-written backprop (NumPy).

This is the QLoRA recipe in miniature: the pretrained weights are frozen (and, for QLoRA, stored in a 4-bit block format from
POC 014 and dequantised on the fly), only the low-rank adapters A (in x r) and B (r x out) and the task head are trained, and
the adapter starts as an exact no-op (B = 0). It is NOT a transformer and does not use PEFT, bitsandbytes or a GPU.
"""

from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass, field

import numpy as np
from gguf_quantisation_study.quant import Packed, dequantise, quantise


def featurize(texts: list[str], dim: int = 1024) -> np.ndarray:
    """Hashed unigram+bigram binary features, L2-normalised (deterministic, no vocabulary to fit)."""
    x = np.zeros((len(texts), dim), np.float32)
    for i, t in enumerate(texts):
        toks = t.lower().replace(".", " ").split()
        for f in toks + [a + "_" + b for a, b in zip(toks, toks[1:], strict=False)]:
            x[i, zlib.crc32(f.encode()) % dim] = 1.0
    n = np.linalg.norm(x, axis=1, keepdims=True)
    return x / np.where(n == 0, 1, n)


def softmax(z: np.ndarray) -> np.ndarray:
    e = np.exp(z - z.max(1, keepdims=True))
    return e / e.sum(1, keepdims=True)


@dataclass
class Layer:
    W: np.ndarray | None  # dense base weights (None when stored quantised)
    b: np.ndarray
    packed: Packed | None = None
    A: np.ndarray | None = None
    B: np.ndarray | None = None
    scale: float = 1.0
    _deq: np.ndarray | None = field(default=None, repr=False)

    @property
    def weight(self) -> np.ndarray:
        if self.packed is not None:
            if self._deq is None:
                self._deq = dequantise(self.packed)
            return self._deq
        return self.W


class LoraNet:
    def __init__(self, sizes: list[int], n_classes: int, seed: int = 0):
        rng = np.random.default_rng(seed)
        self.layers = [
            Layer(
                (rng.normal(0, np.sqrt(2 / a), (a, b))).astype(np.float32), np.zeros(b, np.float32)
            )
            for a, b in zip(sizes[:-1], sizes[1:], strict=True)
        ]
        self.head_W = rng.normal(0, np.sqrt(1 / sizes[-1]), (sizes[-1], n_classes)).astype(
            np.float32
        )
        self.head_b = np.zeros(n_classes, np.float32)
        self.mode = "full"
        self.rng = rng

    # ---- configuration --------------------------------------------------------------------------
    def reset_head(self, n_classes: int, seed: int) -> None:
        rng = np.random.default_rng(seed)
        d = self.head_W.shape[0]
        self.head_W = rng.normal(0, np.sqrt(1 / d), (d, n_classes)).astype(np.float32)
        self.head_b = np.zeros(n_classes, np.float32)

    def add_lora(self, rank: int, alpha: float, seed: int) -> None:
        rng = np.random.default_rng(seed)
        for ly in self.layers:
            a, b = ly.weight.shape
            ly.A = rng.normal(0, 1 / np.sqrt(a), (a, rank)).astype(np.float32)
            ly.B = np.zeros((rank, b), np.float32)  # adapter starts as an exact no-op
            ly.scale = alpha / rank

    def drop_lora(self) -> None:
        for ly in self.layers:
            ly.A = ly.B = None
            ly.scale = 1.0

    def quantise_base(self, fmt: str = "Q4_0") -> int:
        """Replace dense base weights by packed blocks; returns the packed byte size. Biases stay fp32."""
        n = 0
        for ly in self.layers:
            ly.packed = quantise(ly.W, fmt)
            ly.W, ly._deq = None, None
            n += ly.packed.nbytes
        return n

    def base_hash(self) -> str:
        h = hashlib.sha256()
        for ly in self.layers:
            h.update(ly.packed.payload.tobytes() if ly.packed is not None else ly.W.tobytes())
            h.update(ly.b.tobytes())
        return h.hexdigest()[:16]

    def merge(self) -> LoraNet:
        """A new dense net with W + scale*A@B folded in (the adapter-free deployable)."""
        out = LoraNet(
            [self.layers[0].weight.shape[0]] + [ly.weight.shape[1] for ly in self.layers],
            self.head_W.shape[1],
        )
        for src, dst in zip(self.layers, out.layers, strict=True):
            w = src.weight.copy()
            if src.A is not None:
                w = w + src.scale * (src.A @ src.B)
            dst.W, dst.b = w.astype(np.float32), src.b.copy()
        out.head_W, out.head_b = self.head_W.copy(), self.head_b.copy()
        return out

    # ---- parameters -----------------------------------------------------------------------------
    def trainable(self) -> dict[str, np.ndarray]:
        p = {"head_W": self.head_W, "head_b": self.head_b}
        if self.mode == "linear_probe":
            return p
        for i, ly in enumerate(self.layers):
            if self.mode == "full":
                p[f"W{i}"], p[f"b{i}"] = ly.W, ly.b
            else:  # lora / qlora: only adapters
                p[f"A{i}"], p[f"B{i}"] = ly.A, ly.B
        return p

    def n_trainable(self) -> int:
        return int(sum(v.size for v in self.trainable().values()))

    def n_base(self) -> int:
        return int(sum(ly.weight.size + ly.b.size for ly in self.layers))

    # ---- forward / backward ---------------------------------------------------------------------
    def forward(self, x: np.ndarray, keep: bool = False):
        acts, h = [x], x
        for ly in self.layers:
            z = h @ ly.weight + ly.b
            if ly.A is not None:
                z = z + ly.scale * ((h @ ly.A) @ ly.B)
            h = np.maximum(z, 0)
            acts.append(h)
        logits = h @ self.head_W + self.head_b
        return (logits, acts) if keep else logits

    def loss_and_grads(self, x: np.ndarray, y: np.ndarray, l2: float = 0.0):
        logits, acts = self.forward(x, keep=True)
        p = softmax(logits)
        n = len(y)
        loss = float(-np.log(p[np.arange(n), y] + 1e-12).mean())
        d = p.copy()
        d[np.arange(n), y] -= 1
        d /= n
        g = {"head_W": acts[-1].T @ d + l2 * self.head_W, "head_b": d.sum(0)}
        dh = d @ self.head_W.T
        for i in reversed(range(len(self.layers))):
            ly, hin, hout = self.layers[i], acts[i], acts[i + 1]
            dz = dh * (hout > 0)
            if self.mode == "full":
                g[f"W{i}"], g[f"b{i}"] = hin.T @ dz + l2 * ly.W, dz.sum(0)
            if ly.A is not None and self.mode in ("lora", "qlora"):
                ha = hin @ ly.A
                g[f"B{i}"] = ly.scale * (ha.T @ dz)
                dha = ly.scale * (dz @ ly.B.T)
                g[f"A{i}"] = hin.T @ dha
            dh = dz @ ly.weight.T
            if ly.A is not None:
                dh = dh + (ly.scale * (dz @ ly.B.T)) @ ly.A.T
        return loss, {k: v for k, v in g.items() if k in self.trainable()}

    def predict(self, x: np.ndarray) -> np.ndarray:
        return self.forward(x).argmax(1)


class Adam:
    def __init__(
        self, params: dict[str, np.ndarray], lr: float = 1e-2, b1: float = 0.9, b2: float = 0.999
    ):
        self.p, self.lr, self.b1, self.b2, self.t = params, lr, b1, b2, 0
        self.m = {k: np.zeros_like(v) for k, v in params.items()}
        self.v = {k: np.zeros_like(v) for k, v in params.items()}

    def step(self, grads: dict[str, np.ndarray]) -> None:
        self.t += 1
        for k, g in grads.items():
            self.m[k] = self.b1 * self.m[k] + (1 - self.b1) * g
            self.v[k] = self.b2 * self.v[k] + (1 - self.b2) * g * g
            mh, vh = self.m[k] / (1 - self.b1**self.t), self.v[k] / (1 - self.b2**self.t)
            self.p[k] -= self.lr * mh / (np.sqrt(vh) + 1e-8)


def fit(net: LoraNet, x: np.ndarray, y: np.ndarray, *, epochs: int = 30, lr: float = 1e-2, batch: int = 32, seed: int = 0, l2: float = 1e-4,
        val: tuple[np.ndarray, np.ndarray] | None = None, patience: int = 6) -> dict:  # fmt: skip
    """Mini-batch Adam. With `val`, keeps the weights of the best validation epoch (early stopping)."""
    rng = np.random.default_rng(seed)
    opt = Adam(net.trainable(), lr)
    best, best_state, bad, hist = -1.0, None, 0, []
    for _ep in range(epochs):
        order = rng.permutation(len(y))
        tot = 0.0
        for i in range(0, len(y), batch):
            idx = order[i : i + batch]
            loss, g = net.loss_and_grads(x[idx], y[idx], l2)
            opt.step(g)
            tot += loss * len(idx)
        hist.append(tot / len(y))
        if val is not None:
            acc = float((net.predict(val[0]) == val[1]).mean())
            if acc > best:
                best, bad = acc, 0
                best_state = {k: v.copy() for k, v in net.trainable().items()}
            else:
                bad += 1
                if bad >= patience:
                    break
    if best_state is not None:
        for k, v in best_state.items():
            net.trainable()[k][...] = v
    return {"loss": hist, "epochs_run": len(hist), "best_val_acc": best}
