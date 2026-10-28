"""Optional comparison against the real gitleaks binary (skipped when it is not installed)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def binary() -> str | None:
    return os.environ.get("GITLEAKS_BIN") or shutil.which("gitleaks")


def version() -> str | None:
    b = binary()
    if not b:
        return None
    return subprocess.run(
        [b, "version"], capture_output=True, text=True, timeout=20, check=False
    ).stdout.strip()


def scan_dir(directory: Path, config: Path | None = None) -> list[dict]:
    b = binary()
    if not b:
        raise FileNotFoundError("gitleaks not installed")
    report = directory.parent / f"{directory.name}.gitleaks.json"
    cmd = [
        b,
        "dir",
        str(directory),
        "--no-banner",
        "--redact",
        "--report-format",
        "json",
        "--report-path",
        str(report),
        "--exit-code",
        "0",
    ]
    if config:
        cmd += ["--config", str(config)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    if p.returncode != 0:
        raise RuntimeError(f"gitleaks failed: {p.stderr.strip()[:200]}")
    return json.loads(report.read_text() or "[]") if report.exists() else []
