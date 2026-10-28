"""Synthetic daily cost-and-usage data (CUR-like) with labelled anomalies. No real billing data."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DAYS = 180
SERVICES = {
    "EC2": 300.0,
    "S3": 120.0,
    "RDS": 180.0,
    "Glue": 90.0,
    "Athena": 40.0,
    "DataTransfer": 60.0,
    "Lambda": 25.0,
    "KMS": 8.0,
}
ACCOUNTS = {"prod": 0.6, "data": 0.3, "dev": 0.1}
WEEKDAY = np.array([1.05, 1.08, 1.08, 1.06, 1.02, 0.85, 0.82])  # Mon..Sun usage pattern


@dataclass(frozen=True)
class Anomaly:
    kind: str  # spike | step | ramp | new_service
    start: int
    length: int
    service: str
    account: str
    factor: float  # spike/step multiplier; ramp = final multiplier; new_service = daily USD
    note: str = ""


def baseline(rng: np.random.Generator, days: int = DAYS) -> dict[tuple[str, str], np.ndarray]:
    t = np.arange(days)
    out = {}
    for svc, base in SERVICES.items():
        for acct, share in ACCOUNTS.items():
            trend = 1 + 0.0008 * t  # slow organic growth
            season = WEEKDAY[t % 7]
            month_end = 1 + 0.15 * ((t % 30) >= 27) * (
                svc in ("Glue", "Athena")
            )  # month-end batch jobs
            noise = rng.lognormal(0, 0.04, days)
            out[(svc, acct)] = base * share * trend * season * month_end * noise
    return out


def inject(series: dict[tuple[str, str], np.ndarray], anomalies: list[Anomaly]) -> dict:
    out = {k: v.copy() for k, v in series.items()}
    for a in anomalies:
        key = (a.service, a.account)
        if a.kind == "new_service":
            out.setdefault(key, np.zeros_like(next(iter(out.values()))))
            out[key][a.start : a.start + a.length] += a.factor
            continue
        sl = slice(a.start, a.start + a.length)
        n = len(out[key][sl])
        mult = np.full(n, a.factor) if a.kind in ("spike", "step") else np.linspace(1, a.factor, n)
        out[key][sl] = out[key][sl] * mult
    return out


def default_anomalies() -> list[Anomaly]:
    return [
        Anomaly("spike", 60, 2, "EC2", "dev", 6.0, "forgotten GPU fleet"),
        Anomaly("spike", 95, 1, "DataTransfer", "prod", 5.0, "cross-region copy"),
        Anomaly("step", 110, 70, "RDS", "data", 1.35, "instance class upgrade left on"),
        Anomaly("ramp", 120, 40, "S3", "data", 1.8, "unbounded log growth"),
        Anomaly("spike", 75, 1, "S3", "prod", 1.8, "subtle one-day spike"),
        Anomaly("new_service", 140, 40, "Glue", "dev", 45.0, "unexpected new job"),
        Anomaly("spike", 150, 2, "Athena", "prod", 9.0, "full-table scan loop"),
        Anomaly("spike", 30, 1, "Lambda", "dev", 8.0, "retry storm"),
    ]


def total(series: dict) -> np.ndarray:
    return np.sum(list(series.values()), axis=0)


def by(series: dict, idx: int) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for key, v in series.items():
        out[key[idx]] = out.get(key[idx], 0) + v
    return out
