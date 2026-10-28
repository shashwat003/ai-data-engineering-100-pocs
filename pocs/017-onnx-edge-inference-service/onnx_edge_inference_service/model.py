"""Train a small MLP on synthetic tabular data, export to ONNX, quantise to int8 (dynamic)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from onnxruntime.quantization import QuantType, quantize_dynamic
from skl2onnx import to_onnx
from sklearn.neural_network import MLPClassifier

N_FEATURES = 20


def make_data(n: int = 6000, seed: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """Synthetic binary task with feature interactions (so a small MLP has something to learn)."""
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, N_FEATURES)).astype(np.float32)
    logit = (
        1.2 * x[:, 0] * x[:, 1]
        + 0.8 * np.sin(2 * x[:, 2])
        + 0.6 * x[:, 3]
        - 0.5 * x[:, 4] ** 2
        + 0.3 * x[:, 5:8].sum(1)
    )
    y = (logit + rng.normal(0, 0.5, n) > 0.2).astype(int)
    return x, y


def train(x, y, seed: int = 0) -> MLPClassifier:
    return MLPClassifier(
        hidden_layer_sizes=(256, 128), max_iter=120, random_state=seed, early_stopping=True
    ).fit(x, y)


def export(model: MLPClassifier, out_dir: str | Path) -> dict[str, Path]:
    """Write fp32 and dynamically-quantised int8 ONNX files; return their paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fp32, int8 = out / "model_fp32.onnx", out / "model_int8.onnx"
    onx = to_onnx(
        model,
        np.zeros((1, N_FEATURES), dtype=np.float32),
        options={id(model): {"zipmap": False}},
        target_opset=17,
    )
    fp32.write_bytes(onx.SerializeToString())
    quantize_dynamic(str(fp32), str(int8), weight_type=QuantType.QInt8)
    return {"fp32": fp32, "int8": int8}
