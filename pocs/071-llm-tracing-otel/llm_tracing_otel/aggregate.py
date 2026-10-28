"""Turn finished spans into per-route/model cost, latency and error metrics, and flag anomalies."""

from __future__ import annotations

import statistics
from collections import defaultdict


def _pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else 0.0


def summarize(spans) -> dict:
    groups = defaultdict(lambda: {"n": 0, "errors": 0, "cost": 0.0, "lat": [], "in": 0, "out": 0})
    for s in spans:
        a = s.attributes or {}
        if a.get("gen_ai.operation.name") != "chat":
            continue
        g = groups[(a.get("app.route", "?"), a.get("gen_ai.request.model", "?"))]
        g["n"] += 1
        if s.status.status_code.name == "ERROR":
            g["errors"] += 1
            continue
        g["cost"] += a.get("llm.cost_usd", 0.0)
        g["lat"].append(a.get("llm.latency_ms", 0.0))
        g["in"] += a.get("gen_ai.usage.input_tokens", 0)
        g["out"] += a.get("gen_ai.usage.output_tokens", 0)
    out = {}
    for (route, model), g in sorted(groups.items()):
        ok = g["n"] - g["errors"]
        out[f"{route}|{model}"] = {
            "calls": g["n"],
            "error_rate": round(g["errors"] / g["n"], 3),
            "cost_usd": round(g["cost"], 4),
            "cost_per_call": round(g["cost"] / ok, 6) if ok else 0.0,
            "p50_ms": round(_pct(g["lat"], 0.5), 1),
            "p95_ms": round(_pct(g["lat"], 0.95), 1),
            "avg_in_tokens": round(g["in"] / ok, 1) if ok else 0.0,
        }
    return out


def alerts(
    summary: dict,
    *,
    budget_usd: float,
    max_error_rate: float = 0.1,
    p95_ms: float = 2000,
    spike_factor: float = 3.0,
) -> list[str]:
    msgs = []
    total = sum(v["cost_usd"] for v in summary.values())
    if total > budget_usd:
        msgs.append(f"budget exceeded: ${total:.4f} > ${budget_usd:.4f}")
    costs = [v["cost_per_call"] for v in summary.values() if v["cost_per_call"]]
    med = statistics.median(costs) if costs else 0.0
    for k, v in summary.items():
        if v["error_rate"] > max_error_rate:
            msgs.append(f"{k}: error rate {v['error_rate']:.0%}")
        if v["p95_ms"] > p95_ms:
            msgs.append(f"{k}: p95 latency {v['p95_ms']:.0f} ms")
        if med and len(costs) >= 3 and v["cost_per_call"] > spike_factor * med:
            msgs.append(
                f"{k}: cost/call {v['cost_per_call']:.5f} is {v['cost_per_call'] / med:.1f}x the median route"
            )
    return msgs
