"""Drain3 template mining + count-based anomaly rules (new template, rate spike, heartbeat loss)."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from drain3 import TemplateMiner
from drain3.masking import MaskingInstruction
from drain3.template_miner_config import TemplateMinerConfig


def make_miner() -> TemplateMiner:
    cfg = TemplateMinerConfig()
    cfg.profiling_enabled = False
    cfg.drain_sim_th = 0.5
    cfg.drain_depth = 4
    cfg.masking_instructions = [
        MaskingInstruction(r"\b\d{1,3}(\.\d{1,3}){3}\b", "IP"),
        MaskingInstruction(r"\b[0-9a-f]{8}\b", "HEX"),
        MaskingInstruction(r"\b\d+\b", "NUM"),
    ]
    return TemplateMiner(config=cfg)


def mine(lines, miner: TemplateMiner | None = None):
    """Assign each line to a Drain cluster id; returns (miner, [(minute, service, cluster_id)])."""
    miner = miner or make_miner()
    out = []
    for ln in lines:
        r = miner.add_log_message(f"{ln.service} {ln.text}")
        out.append((ln.minute, ln.service, r["cluster_id"]))
    return miner, out


def detect(
    assigned, train_minutes: int, z: float = 5.0, min_hb: float = 1.0, hb_run: int = 3
) -> dict:
    """Return {minute: [reasons]} for test minutes. Baseline = per-template per-minute counts in training."""
    counts = defaultdict(Counter)  # minute -> cluster -> n
    for m, _s, c in assigned:
        counts[m][c] += 1
    last = max(counts) + 1
    train = range(train_minutes)
    base_mu = Counter()
    for m in train:
        for c, n in counts[m].items():
            base_mu[c] += n / train_minutes
    flags: dict[int, list[str]] = defaultdict(list)
    run: Counter = Counter()
    for m in range(train_minutes, last):
        for c, n in counts[m].items():
            if c not in base_mu:
                flags[m].append(f"new_template:{c}")
            elif (n - base_mu[c]) / math.sqrt(base_mu[c] + 1) >= z:
                flags[m].append(f"spike:{c}")
        for c, mu in base_mu.items():
            if mu >= min_hb and counts[m].get(c, 0) == 0:
                run[c] += 1
                if run[c] >= hb_run:
                    flags[m].append(f"lost:{c}")
            else:
                run[c] = 0
    return dict(flags)


def prf(flags: dict, truth: list[int], train_minutes: int, minutes: int) -> dict:
    test = range(train_minutes, minutes)
    pred = {m for m in flags if m in test}
    t = set(truth)
    tp, fp, fn = len(pred & t), len(pred - t), len(t - pred)
    return {
        "precision": round(tp / max(1, tp + fp), 3),
        "recall": round(tp / max(1, tp + fn), 3),
        "false_positive_windows": fp,
        "missed_windows": sorted(t - pred),
    }


_SECRET = re.compile(
    r"(?i)\b(?:\d{1,3}\.){3}\d{1,3}\b|[\w.+-]+@[\w-]+\.[\w.]+|\b(?:token|key|secret|password)=\S+"
)


def sanitize(text: str) -> str:
    return _SECRET.sub("<redacted>", text)[:300]
