"""Build the edge (int8 ONNX, real) and cloud (gradient boosting, stands in for a large hosted model) models, evaluate policies."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from drift_monitoring.monitor import Monitor, Policy
from onnx_edge_inference_service.engine import OnnxModel
from onnx_edge_inference_service.model import export
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.neural_network import MLPClassifier

from .router import Costs, HybridRouter
from .stream import as_columns, window

WINDOW = 500


@dataclass
class Models:
    edge: OnnxModel
    cloud: HistGradientBoostingClassifier
    reference: dict[str, np.ndarray]
    val: tuple[np.ndarray, np.ndarray]


def build_models(workdir: str | Path | None = None, seed: int = 0, n_train: int = 6000) -> Models:
    rng = np.random.default_rng(seed)
    xtr, ytr = window(rng, n_train)
    xcloud, ycloud = window(rng, 3 * n_train)  # the cloud model has more data and capacity
    xval, yval = window(rng, 3000)
    mlp = MLPClassifier(
        hidden_layer_sizes=(16, 8), max_iter=200, random_state=seed, early_stopping=True
    ).fit(xtr, ytr)
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp())
    edge = OnnxModel(export(mlp, wd)["int8"])
    cloud = HistGradientBoostingClassifier(max_iter=300, random_state=seed).fit(
        np.vstack([xtr, xcloud]), np.concatenate([ytr, ycloud])
    )
    return Models(edge, cloud, as_columns(xval[:2000]), (xval, yval))


def edge_proba(model: OnnxModel):
    def f(x: np.ndarray) -> np.ndarray:
        return model.predict_proba(x)

    return f


def batch_edge(model: OnnxModel, x: np.ndarray) -> np.ndarray:
    return np.concatenate([model.predict_proba(x[i : i + 256]) for i in range(0, len(x), 256)])


def calibrate_threshold(models: Models, target_gap: float = 0.01) -> dict:
    """Smallest confidence threshold whose validation accuracy is within `target_gap` of cloud-only."""
    xv, yv = models.val
    pe = batch_edge(models.edge, xv)
    conf, edge_lab = np.maximum(pe, 1 - pe), (pe > 0.5).astype(int)
    cloud_lab = models.cloud.predict(xv)
    cloud_acc = float((cloud_lab == yv).mean())
    sweep = []
    chosen = None
    for th in np.round(np.arange(0.5, 1.0001, 0.05), 2):
        esc = conf < th
        acc = float((np.where(esc, cloud_lab, edge_lab) == yv).mean())
        sweep.append(
            {
                "threshold": float(th),
                "cloud_share": round(float(esc.mean()), 3),
                "accuracy": round(acc, 4),
            }
        )
        if chosen is None and acc >= cloud_acc - target_gap:
            chosen = float(th)
    return {
        "edge_accuracy": round(float((edge_lab == yv).mean()), 4),
        "cloud_accuracy": round(cloud_acc, 4),
        "sweep": sweep,
        "threshold": chosen if chosen is not None else 1.0,
    }


def make_router(
    models: Models, threshold: float, costs: Costs | None = None, cloud_fn=None
) -> HybridRouter:
    cf = cloud_fn or (lambda x: int(models.cloud.predict(x.reshape(1, -1))[0]))
    return HybridRouter(edge_proba(models.edge), cf, threshold, costs or Costs())


def serve(router: HybridRouter, x: np.ndarray, y: np.ndarray) -> dict:
    ds = [router.predict(row) for row in x]
    src = [d.source for d in ds]
    lat = np.array([d.latency_ms for d in ds])
    return {
        "accuracy": float(np.mean([d.label == t for d, t in zip(ds, y, strict=True)])),
        "cloud_share": src.count("cloud") / len(ds),
        "degraded_share": src.count("edge-degraded") / len(ds),
        "mean_latency_ms": float(lat.mean()),
        "p95_latency_ms": float(np.percentile(lat, 95)),
        "usd_per_1k": float(sum(d.cost_usd for d in ds) / len(ds) * 1000),
    }


def drift_scenario(models: Models, base_threshold: float, hard_share: float, controlled: bool, seed: int = 7,
                   windows: int = 12, onset: int = 4, tight_threshold: float = 0.95) -> list[dict]:  # fmt: skip
    """Stream `windows` windows; a traffic-mix shift (`hard_share` of requests from the hard region) starts at window `onset`.

    controlled: when POC 066's monitor raises an alert, the router tightens its threshold (more cloud traffic);
    when no feature alerts for two windows it relaxes back."""
    rng = np.random.default_rng(seed)
    router = make_router(models, base_threshold)
    mon = Monitor(models.reference, Policy(alpha=0.01, psi_threshold=0.2, persistence=2))
    quiet, rows = 0, []
    for w in range(windows):
        x, y = window(rng, WINDOW, hard_share if w >= onset else 0.0)
        res = mon.check_window(as_columns(x))
        alerting = sorted(k for k, v in res.items() if v["alert"])
        if controlled:
            if alerting:
                router.threshold, quiet = tight_threshold, 0
            else:
                quiet += 1
                if quiet >= 2:
                    router.threshold = base_threshold
        s = serve(router, x, y)
        rows.append({"window": w, "drifted": w >= onset, "alerting_features": len(alerting),
                     "threshold": router.threshold, **{k: round(v, 4) for k, v in s.items()}})  # fmt: skip
    return rows
