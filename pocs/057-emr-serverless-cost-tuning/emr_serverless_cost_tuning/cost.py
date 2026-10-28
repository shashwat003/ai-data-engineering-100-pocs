"""EMR Serverless cost model and plan checks. PRICES ARE ASSUMED LIST PRICES (us-east-1 style, ARM64 discount assumed); check the current price list."""

from __future__ import annotations

from dataclasses import dataclass

VCPU_HOUR = 0.052624
GB_HOUR = 0.0057785
STORAGE_GB_HOUR = 0.000111  # beyond the free 20 GB per worker
ARM_DISCOUNT = 0.80  # ASSUMED: ARM64 workers priced 20% lower
MIN_BILLED_S = 60  # each worker is billed for at least one minute
STARTUP_S = 15.0  # ASSUMED worker start-up overhead per run when no pre-initialised capacity


@dataclass(frozen=True)
class Worker:
    vcpu: int = 4
    memory_gb: int = 16
    disk_gb: int = 20
    arm: bool = True

    @property
    def hourly(self) -> float:
        base = (
            self.vcpu * VCPU_HOUR
            + self.memory_gb * GB_HOUR
            + max(0, self.disk_gb - 20) * STORAGE_GB_HOUR
        )
        return base * (ARM_DISCOUNT if self.arm else 1.0)


def job_cost(
    runtime_s: float, workers: int, w: Worker | None = None, preinit: bool = False
) -> dict:
    """Cost of ONE job run: every worker is billed for max(runtime + start-up, 60 s)."""
    w = w or Worker()
    billed = max(runtime_s + (0 if preinit else STARTUP_S), MIN_BILLED_S)
    cost = workers * w.hourly * billed / 3600
    return {
        "billed_s": round(billed, 1),
        "cost_usd": round(cost, 5),
        "vcpu_seconds": round(workers * w.vcpu * billed, 1),
    }


def idle_cost_per_month(preinit_workers: int, w: Worker | None = None) -> float:
    """Pre-initialised workers are billed while idle: the price of hiding start-up latency."""
    return round(preinit_workers * (w or Worker()).hourly * 24 * 30, 2)


def cheapest(
    measurements: list[dict], key: str = "runtime_s", workers_key: str = "workers"
) -> dict:
    return min(measurements, key=lambda m: job_cost(m[key], m[workers_key])["cost_usd"])


def check_plan(plan: dict, *, max_vcpu: int = 128, max_idle_minutes: int = 15) -> list[str]:
    out = []
    apps = [
        r["change"]["after"]
        for r in plan["resource_changes"]
        if r["type"] == "aws_emrserverless_application" and "delete" not in r["change"]["actions"]
    ]
    if not apps:
        return ["no EMR Serverless application in the plan"]
    for a in apps:
        stop = (a.get("auto_stop_configuration") or [{}])[0]
        if not stop.get("enabled"):
            out.append(
                "auto-stop is disabled: an idle application can keep billing for pre-initialised capacity"
            )
        elif stop.get("idle_timeout_minutes", 15) > max_idle_minutes:
            out.append(
                f"idle timeout {stop['idle_timeout_minutes']} min exceeds {max_idle_minutes}"
            )
        cap = (a.get("maximum_capacity") or [None])[0]
        if not cap:
            out.append("no maximum capacity: a runaway job can scale without a cost ceiling")
        elif int(str(cap["cpu"]).split()[0]) > max_vcpu:
            out.append(f"maximum capacity {cap['cpu']} exceeds the {max_vcpu} vCPU ceiling")
        if a.get("architecture") != "ARM64":
            out.append(
                "x86_64 workers: ARM64 (Graviton) is cheaper for Spark if libraries support it"
            )
        pre = a.get("initial_capacity") or []
        for ic in pre:
            cfg = (ic.get("initial_capacity_config") or [{}])[0]
            n = cfg.get("worker_count", 0)
            if n:
                out.append(
                    f"{n} pre-initialised workers kept warm: about ${idle_cost_per_month(n)} per month while idle (justify with a latency requirement)"
                )
        if not a.get("tags", {}).get("cost-centre"):
            out.append("no cost-centre tag")
    return out
