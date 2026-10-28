"""Benchmark runner: latency, throughput, quality (with CIs), memory and a Pareto view per model."""

from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass, field

import pandas as pd

from llm_client import LLMError, model_matches

from .tasks import Task


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


@dataclass
class Sample:
    model: str
    task: str
    category: str
    repeat: int
    ok: bool
    error: str | None
    latency_ms: float
    out_tokens: int
    tok_per_s: float
    text: str = ""


@dataclass
class Report:
    samples: list[Sample] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    memory_mb: dict[str, float | None] = field(default_factory=dict)


def memory_mb(client, model: str) -> float | None:
    """Loaded size reported by the server (/api/ps). None when unavailable: never guessed."""
    try:
        for m in client._request("/api/ps").get("models", []):
            if model_matches(m.get("name", ""), model):
                return round(m["size"] / 1e6, 1)
    except (LLMError, KeyError, TypeError):
        pass
    return None


def run_benchmark(
    client,
    models: list[str],
    tasks: list[Task],
    repeats: int = 3,
    max_tokens: int = 128,
    warmup: int = 1,
) -> Report:
    rep = Report()
    try:
        installed = [m["name"] for m in client.list_models()]
    except LLMError as e:
        for m in models:
            rep.skipped[m] = f"server unreachable: {str(e)[:80]}"
        return rep
    for model in models:
        if not any(model_matches(i, model) for i in installed):
            rep.skipped[model] = "not installed"
            continue
        try:
            for _ in range(warmup):  # load the model so the first sample is not a cold start
                client.generate("Say OK.", model=model, max_tokens=4)
        except LLMError as e:
            rep.skipped[model] = f"warm-up failed: {str(e)[:80]}"
            continue
        for task in tasks:
            for r in range(repeats):
                t0 = time.perf_counter()
                try:
                    resp = client.generate(
                        task.prompt, model=model, temperature=0.0, max_tokens=max_tokens, seed=r
                    )
                    lat = (time.perf_counter() - t0) * 1000
                    ok, err, text, ntok = task.check(resp.text), None, resp.text, resp.output_tokens
                except LLMError as e:
                    lat = (time.perf_counter() - t0) * 1000
                    ok, err, text, ntok = False, type(e).__name__, "", 0
                rep.samples.append(
                    Sample(
                        model,
                        task.id,
                        task.category,
                        r,
                        ok,
                        err,
                        lat,
                        ntok,
                        ntok / (lat / 1000) if lat > 0 else 0.0,
                        text[:200],
                    )
                )
        rep.memory_mb[model] = memory_mb(client, model)
    return rep


def summarize(rep: Report) -> pd.DataFrame:
    if not rep.samples:
        return pd.DataFrame()
    df = pd.DataFrame([s.__dict__ for s in rep.samples])
    rows = []
    for m, g in df.groupby("model"):
        k, n = int(g.ok.sum()), len(g)
        lo, hi = wilson(k, n)
        lat = g.latency_ms.tolist()
        by_task = g.groupby("task").text.nunique()
        rows.append({
            "model": m, "n": n, "pass_rate": round(k / n, 3), "ci_lo": round(lo, 3), "ci_hi": round(hi, 3),
            "errors": int(g.error.notna().sum()),
            "p50_ms": round(statistics.median(lat), 1), "p95_ms": round(sorted(lat)[max(0, math.ceil(0.95 * n) - 1)], 1),
            "tok_per_s": round(g.tok_per_s.median(), 1), "memory_mb": rep.memory_mb.get(m),
            "unstable_tasks": int((by_task > 1).sum()),
        })  # fmt: skip
    out = pd.DataFrame(rows).sort_values("pass_rate", ascending=False).reset_index(drop=True)
    out["pareto"] = pareto(out)
    return out


def pareto(df: pd.DataFrame) -> list[bool]:
    """Non-dominated on (higher pass_rate, lower p50 latency, lower memory when known)."""
    flags = []
    for i, a in df.iterrows():
        dominated = False
        for j, b in df.iterrows():
            if i == j:
                continue
            mem_a = (
                a.memory_mb if a.memory_mb == a.memory_mb and a.memory_mb is not None else math.inf
            )
            mem_b = (
                b.memory_mb if b.memory_mb == b.memory_mb and b.memory_mb is not None else math.inf
            )
            ge = b.pass_rate >= a.pass_rate and b.p50_ms <= a.p50_ms and mem_b <= mem_a
            gt = b.pass_rate > a.pass_rate or b.p50_ms < a.p50_ms or mem_b < mem_a
            if ge and gt:
                dominated = True
                break
        flags.append(not dominated)
    return flags


def by_category(rep: Report) -> pd.DataFrame:
    df = pd.DataFrame([s.__dict__ for s in rep.samples])
    return df.pivot_table(index="model", columns="category", values="ok", aggfunc="mean").round(2)
