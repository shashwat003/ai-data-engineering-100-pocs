"""Run untrusted Python (e.g. agent-written analysis code) with layered, individually testable defences.

NOT a security boundary against a determined attacker: Python-level sandboxes are escapable and rlimits
do not stop everything. Use a container/microVM (gVisor, Firecracker) for real isolation. This POC
shows the layers, measures which one stops which attack, and keeps cheap defence-in-depth on.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .policy import ALLOWED_IMPORTS, PolicyViolation, check

RUNNER = Path(__file__).with_name("_runner.py")


@dataclass(frozen=True)
class Layers:
    static_policy: bool = True
    restricted_builtins: bool = True
    rlimits: bool = True
    network_isolation: bool = False  # `unshare -n`; only used when the platform allows it
    wall_clock: bool = True


@dataclass
class Result:
    status: str  # ok | rejected | timeout | error | killed | output_truncated
    stdout: str = ""
    stderr: str = ""
    stopped_by: str = ""  # which layer ended it (empty if it ran to completion)
    seconds: float = 0.0
    exit_code: int | None = None


def netns_available() -> bool:
    if not shutil.which("unshare"):
        return False
    return subprocess.run(["unshare", "-n", "true"], capture_output=True).returncode == 0


def run(
    code: str,
    *,
    cpu_s: int = 2,
    mem_mb: int = 256,
    wall_s: float = 5.0,
    out_bytes: int = 65_536,
    recursion: int = 200,
    layers: Layers | None = None,
) -> Result:
    layers = layers or Layers()
    if layers.static_policy:
        try:
            check(code)
        except PolicyViolation as e:
            return Result("rejected", stderr=str(e), stopped_by="static_policy")
    with tempfile.TemporaryDirectory(prefix="sbx-") as tmp:
        f = Path(tmp) / "guest.py"
        f.write_text(code)
        cfg = {"cpu_s": cpu_s, "mem_mb": mem_mb, "rlimits": layers.rlimits, "restricted_builtins": layers.restricted_builtins,
               "allowed_imports": sorted(ALLOWED_IMPORTS), "recursion": recursion}  # fmt: skip
        cmd = [sys.executable, "-I", str(RUNNER), str(f), json.dumps(cfg)]
        if layers.network_isolation and netns_available():
            cmd = ["unshare", "-n", *cmd]
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}  # no inherited secrets
        t0 = time.perf_counter()
        p = subprocess.Popen(
            cmd,
            cwd=tmp,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            out, err = p.communicate(timeout=wall_s if layers.wall_clock else None)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)  # kills any children too
            p.communicate()
            return Result(
                "timeout", stopped_by="wall_clock", seconds=round(time.perf_counter() - t0, 2)
            )
        dt = round(time.perf_counter() - t0, 2)
    trunc = len(out) > out_bytes
    r = Result(
        "ok",
        out[:out_bytes].decode("utf-8", "replace"),
        err[:2000].decode("utf-8", "replace"),
        seconds=dt,
        exit_code=p.returncode,
    )
    if trunc:
        r.status, r.stopped_by = "output_truncated", "output_cap"
    if p.returncode < 0:  # killed by a signal: SIGXCPU/SIGKILL from rlimits or the OOM killer
        r.status, r.stopped_by = "killed", "rlimit" if layers.rlimits else "signal"
    elif p.returncode != 0:
        r.status = "error"
        r.stopped_by = (
            "runtime_restriction"
            if any(
                k in r.stderr
                for k in (
                    "NameError",
                    "ImportError",
                    "MemoryError",
                    "RecursionError",
                    "OSError",
                    "PermissionError",
                )
            )
            else ""
        )
    return r
