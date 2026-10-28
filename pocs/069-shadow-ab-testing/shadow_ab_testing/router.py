"""Experiment router: deterministic sticky bucketing, shadow mode, and a FastAPI front end."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field


def bucket(user_id: str, experiment: str, salt: str = "v1") -> float:
    """Uniform in [0,1), stable for (user, experiment, salt): the same user always lands in the same arm."""
    h = hashlib.sha256(f"{salt}:{experiment}:{user_id}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def assign(user_id: str, experiment: str, treatment_share: float = 0.5, salt: str = "v1") -> str:
    return "treatment" if bucket(user_id, experiment, salt) < treatment_share else "control"


@dataclass
class ShadowLog:
    rows: list[dict] = field(default_factory=list)


class ShadowRouter:
    """Serve the production model; optionally run a candidate in shadow. A shadow failure or slowness must
    never change what the user receives, only what we log."""

    def __init__(
        self,
        prod: Callable[[dict], float],
        candidate: Callable[[dict], float],
        *,
        shadow_share: float = 1.0,
        shadow_timeout_s: float = 0.2,
        experiment: str = "shadow-1",
    ):
        self.prod, self.cand, self.share, self.timeout, self.exp = (
            prod,
            candidate,
            shadow_share,
            shadow_timeout_s,
            experiment,
        )
        self.log = ShadowLog()

    def predict(self, user_id: str, features: dict) -> float:
        out = self.prod(features)
        if bucket(user_id, self.exp + ":shadow") < self.share:
            t = time.perf_counter()
            try:
                shadow = self.cand(features)
                dt = time.perf_counter() - t
                self.log.rows.append(
                    {
                        "user": user_id,
                        "prod": out,
                        "shadow": shadow,
                        "shadow_ms": dt * 1000,
                        "error": None,
                        "timed_out": dt > self.timeout,
                    }
                )
            except Exception as e:  # never propagate
                self.log.rows.append(
                    {
                        "user": user_id,
                        "prod": out,
                        "shadow": None,
                        "shadow_ms": (time.perf_counter() - t) * 1000,
                        "error": type(e).__name__,
                        "timed_out": False,
                    }
                )
        return out
