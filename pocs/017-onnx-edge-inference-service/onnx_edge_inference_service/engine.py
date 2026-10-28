from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort

from .model import N_FEATURES

MAX_BATCH = 256


class InputError(ValueError):
    """Bad request payload; message is safe to return to the client."""


def validate(x) -> np.ndarray:
    try:
        a = np.asarray(x, dtype=np.float32)
    except (TypeError, ValueError) as e:
        raise InputError("features must be numeric") from e
    if a.ndim != 2 or a.shape[1] != N_FEATURES:
        raise InputError(f"expected shape (n, {N_FEATURES})")
    if not 1 <= a.shape[0] <= MAX_BATCH:
        raise InputError(f"batch size must be 1..{MAX_BATCH}")
    if not np.isfinite(a).all():
        raise InputError("features must be finite")
    if np.abs(a).max() > 1e4:
        raise InputError("feature magnitude out of range")
    return a


class OnnxModel:
    def __init__(self, path: str | Path, threads: int = 1):
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        self.path = Path(path)
        self.session = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def predict_proba(self, x) -> np.ndarray:
        a = validate(x)
        probs = self.session.run(None, {self.input_name: a})[1]
        return np.asarray(probs)[:, 1]

    @property
    def size_kb(self) -> float:
        return round(self.path.stat().st_size / 1024, 1)
