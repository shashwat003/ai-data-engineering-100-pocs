"""Run an experiment in fresh interpreter processes under varied environments and compare the outputs."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

POC = Path(__file__).resolve().parent.parent


def run_once(name: str, cfg: dict, env_extra: dict[str, str]) -> str:
    env = {**os.environ, "PYTHONPATH": f"{POC}:{POC.parent.parent / 'libs'}", **env_extra}
    p = subprocess.run(
        [sys.executable, "-m", "reproducibility_kit.experiments", name, json.dumps(cfg)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=POC,
        check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(f"experiment {name} failed: {p.stderr.strip()[-200:]}")
    return p.stdout.strip()


def check(name: str, cfg: dict, hashseeds=("0", "12345"), threads=("1", "4")) -> dict:
    """Same code, data and seed; vary PYTHONHASHSEED and thread count. Identical output = reproducible under those variations."""
    outs = {}
    for hs in hashseeds:
        for th in threads:
            outs[f"hashseed={hs},threads={th}"] = run_once(
                name,
                cfg,
                {
                    "PYTHONHASHSEED": hs,
                    "OMP_NUM_THREADS": th,
                    "OPENBLAS_NUM_THREADS": th,
                    "MKL_NUM_THREADS": th,
                },
            )
    distinct = sorted(set(outs.values()))
    return {
        "experiment": name,
        "runs": len(outs),
        "distinct_outputs": len(distinct),
        "reproducible": len(distinct) == 1,
        "digests": [hashlib.sha256(o.encode()).hexdigest()[:10] for o in distinct],
    }


def diagnose(name: str, cfg: dict) -> dict:
    """Which variation breaks it? First repeat with everything fixed; only if that is stable, vary one factor at a time."""
    base = {"PYTHONHASHSEED": "0", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}
    repeat = {run_once(name, cfg, base) for _ in range(3)}
    if len(repeat) > 1:  # other factors cannot be separated while the run is not even repeatable
        return {
            "experiment": name,
            "repeat_identical": False,
            "hashseed_sensitive": None,
            "thread_sensitive": None,
            "likely_causes": [
                "run-to-run randomness with everything fixed (unseeded RNG, clock, thread race)"
            ],
        }
    hashseed = {run_once(name, cfg, {**base, "PYTHONHASHSEED": hs}) for hs in ("0", "7")}
    threads = {
        run_once(name, cfg, {**base, "OMP_NUM_THREADS": t, "OPENBLAS_NUM_THREADS": t})
        for t in ("1", "4")
    }
    causes = []
    if len(hashseed) > 1:
        causes.append("PYTHONHASHSEED (set/dict-of-str iteration order)")
    if len(threads) > 1:
        causes.append("thread count (parallel reduction order)")
    return {
        "experiment": name,
        "repeat_identical": True,
        "hashseed_sensitive": len(hashseed) > 1,
        "thread_sensitive": len(threads) > 1,
        "likely_causes": causes,
    }
