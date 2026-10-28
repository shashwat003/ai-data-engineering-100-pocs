"""Labelled request streams with controllable covariate drift (same task as POC 017's make_data)."""

from __future__ import annotations

import numpy as np
from onnx_edge_inference_service.model import N_FEATURES

FEATURE_NAMES = tuple(f"f{i}" for i in range(N_FEATURES))


def true_logit(x: np.ndarray) -> np.ndarray:
    return (
        1.2 * x[:, 0] * x[:, 1]
        + 0.8 * np.sin(2 * x[:, 2])
        + 0.6 * x[:, 3]
        - 0.5 * x[:, 4] ** 2
        + 0.3 * x[:, 5:8].sum(1)
    )


def window(
    rng: np.random.Generator, n: int, hard_share: float = 0.0
) -> tuple[np.ndarray, np.ndarray]:
    """n labelled requests. `hard_share` of them come from a narrow region near the decision boundary
    (f0 ~ N(0, 0.3), f2 ~ N(pi/4, 0.3)): a traffic-mix shift towards hard cases."""
    x = rng.normal(size=(n, N_FEATURES)).astype(np.float32)
    hard = rng.random(n) < hard_share
    x[hard, 0] = rng.normal(0, 0.3, hard.sum())
    x[hard, 2] = np.pi / 4 + rng.normal(0, 0.3, hard.sum())
    y = (true_logit(x) + rng.normal(0, 0.5, n) > 0.2).astype(int)
    return x, y


def as_columns(x: np.ndarray) -> dict[str, np.ndarray]:
    return {name: x[:, i].astype(float) for i, name in enumerate(FEATURE_NAMES)}
