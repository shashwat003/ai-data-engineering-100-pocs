"""Seeded streams with injected drift of known type, onset and size, and the Monte-Carlo study."""

from __future__ import annotations

import numpy as np

from .monitor import Monitor, Policy

FEATURES = ("age", "income", "tenure", "score", "plan", "channel")
N_REF, N_WIN = 5_000, 500


def make_data(
    rng: np.random.Generator, n: int, drift: str | None = None, size: float = 0.0
) -> dict[str, np.ndarray]:
    age = rng.normal(40, 10, n)
    income = rng.lognormal(10.5, 0.5, n)
    tenure = rng.exponential(24, n)
    score = rng.beta(2, 5, n)
    plan = rng.choice(["free", "pro", "team", "enterprise"], n, p=[0.5, 0.3, 0.15, 0.05])
    channel = rng.choice(["web", "app", "partner"], n, p=[0.5, 0.4, 0.1])
    if drift == "mean_shift":
        age = age + size * 10
    elif drift == "variance_change":
        age = 40 + (age - 40) * (1 + size)
    elif drift == "categorical_shift":
        plan = rng.choice(
            ["free", "pro", "team", "enterprise"],
            n,
            p=np.array([0.5 - size, 0.3, 0.15, 0.05 + size]).clip(0.001),
        )
        plan = plan
    elif drift == "missing_rate":
        mask = rng.random(n) < size
        income = np.where(mask, np.nan, income)
    elif drift == "heavy_tail":
        income = income * np.where(rng.random(n) < size, 5.0, 1.0)
    return {
        "age": age,
        "income": income,
        "tenure": tenure,
        "score": score,
        "plan": plan,
        "channel": channel,
    }


def run_stream(
    seed: int,
    drift: str | None,
    size: float,
    onset: int = 5,
    windows: int = 12,
    policy: Policy | None = None,
) -> dict:
    rng = np.random.default_rng(seed)
    mon = Monitor(make_data(rng, N_REF), policy or Policy())
    first_alert = None
    alerts_before = 0
    for w in range(windows):
        res = mon.check_window(make_data(rng, N_WIN, drift if w >= onset else None, size))
        alerted = [f for f, r in res.items() if r["alert"]]
        if alerted and w < onset:
            alerts_before += 1
        if alerted and w >= onset and first_alert is None:
            first_alert = w - onset
    return {"first_alert_delay": first_alert, "false_alert_windows": alerts_before}


def study(trials: int, drift: str | None, size: float, **kw) -> dict:
    runs = [run_stream(1000 + i, drift, size, **kw) for i in range(trials)]
    detected = [r["first_alert_delay"] for r in runs if r["first_alert_delay"] is not None]
    return {
        "trials": trials,
        "detection_rate": round(len(detected) / trials, 3),
        "median_delay_windows": float(np.median(detected)) if detected else None,
        "streams_with_pre_onset_alert": round(
            sum(r["false_alert_windows"] > 0 for r in runs) / trials, 3
        ),
    }
