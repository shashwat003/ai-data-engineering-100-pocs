from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from sklearn.metrics import accuracy_score, roc_auc_score

from .engine import OnnxModel
from .model import export, make_data, train


def latency(fn, x, n: int = 300, warm: int = 30) -> dict:
    for _ in range(warm):
        fn(x)
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn(x)
        ts.append((time.perf_counter() - t0) * 1000)
    a = np.array(ts)
    return {
        "p50_ms": round(float(np.percentile(a, 50)), 4),
        "p95_ms": round(float(np.percentile(a, 95)), 4),
        "p99_ms": round(float(np.percentile(a, 99)), 4),
    }


def run(artifact_dir: str | Path, n_lat: int = 300) -> dict:
    x, y = make_data()
    xtr, ytr, xte, yte = x[:4500], y[:4500], x[4500:], y[4500:]
    sk = train(xtr, ytr)
    paths = export(sk, artifact_dir)
    models = {k: OnnxModel(p) for k, p in paths.items()}
    ref = sk.predict_proba(xte)[:, 1]
    out = {"test_rows": len(xte), "variants": {}}
    variants = {
        "sklearn fp32": (lambda a: sk.predict_proba(a)[:, 1], None),
        "onnx fp32": (models["fp32"].predict_proba, models["fp32"]),
        "onnx int8 (dynamic)": (models["int8"].predict_proba, models["int8"]),
    }
    for name, (fn, om) in variants.items():
        p = np.concatenate(
            [fn(xte[i : i + 256]) for i in range(0, len(xte), 256)]
        )  # service batch cap is 256
        out["variants"][name] = {
            "accuracy": round(float(accuracy_score(yte, p >= 0.5)), 4), "auc": round(float(roc_auc_score(yte, p)), 4),
            "max_abs_diff_vs_sklearn": round(float(np.abs(p - ref).max()), 6),
            "agreement_with_sklearn": round(float(((p >= 0.5) == (ref >= 0.5)).mean()), 4),
            "size_kb": om.size_kb if om else None,
            "batch1": latency(fn, xte[:1], n_lat), "batch32": latency(fn, xte[:32], n_lat // 3),
        }  # fmt: skip
    return out
