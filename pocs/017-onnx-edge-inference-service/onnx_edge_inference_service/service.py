"""FastAPI service around an ONNX model with a rolling latency SLO tracker."""

from __future__ import annotations

import time
from collections import deque

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .engine import MAX_BATCH, InputError, OnnxModel
from .model import N_FEATURES


class SloTracker:
    def __init__(self, p99_budget_ms: float, window: int = 1000):
        self.budget, self.samples = p99_budget_ms, deque(maxlen=window)

    def record(self, ms: float) -> None:
        self.samples.append(ms)

    def snapshot(self) -> dict:
        if not self.samples:
            return {"n": 0, "slo_met": None, "budget_p99_ms": self.budget}
        s = np.array(self.samples)
        p50, p95, p99 = (float(np.percentile(s, q)) for q in (50, 95, 99))
        return {
            "n": len(s),
            "p50_ms": round(p50, 3),
            "p95_ms": round(p95, 3),
            "p99_ms": round(p99, 3),
            "budget_p99_ms": self.budget,
            "slo_met": p99 <= self.budget,
        }


class PredictRequest(BaseModel):
    features: list[list[float]] = Field(min_length=1, max_length=MAX_BATCH)


def create_app(model: OnnxModel, p99_budget_ms: float = 50.0) -> FastAPI:
    app = FastAPI(title="onnx-edge-inference", docs_url=None, redoc_url=None)
    slo = SloTracker(p99_budget_ms)

    @app.get("/health")
    def health():
        return {"status": "ok", "model": model.path.name, "n_features": N_FEATURES}

    @app.get("/metrics")
    def metrics():
        return slo.snapshot()

    @app.post("/predict")
    def predict(req: PredictRequest):
        t0 = time.perf_counter()
        try:
            p = model.predict_proba(req.features)
        except InputError as e:
            raise HTTPException(422, str(e)) from e
        slo.record((time.perf_counter() - t0) * 1000)
        return {
            "probabilities": [round(float(v), 6) for v in p],
            "label": [int(v >= 0.5) for v in p],
        }

    @app.exception_handler(Exception)
    async def unhandled(_: Request, __: Exception):
        return JSONResponse(
            {"detail": "internal error"}, status_code=500
        )  # no stack traces to clients

    app.state.slo = slo
    return app
