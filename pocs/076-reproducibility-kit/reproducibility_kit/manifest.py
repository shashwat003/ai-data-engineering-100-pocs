"""A run manifest: everything needed to decide later whether two runs SHOULD give the same answer."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path

RELEVANT_ENV = (
    "PYTHONHASHSEED",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "CUBLAS_WORKSPACE_CONFIG",
)


def sha256_file(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def sha256_tree(root: Path, suffixes: tuple[str, ...] = (".py",)) -> str:
    """Hash of file names and contents under root (sorted, so independent of directory order)."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix in suffixes and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(root)).encode())
            h.update(sha256_file(p).encode())
    return h.hexdigest()


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def packages(names: list[str] | None = None) -> dict[str, str]:
    if names is None:
        return {
            d.metadata["Name"].lower(): d.version
            for d in metadata.distributions()
            if d.metadata["Name"]
        }
    out = {}
    for n in names:
        try:
            out[n.lower()] = metadata.version(n)
        except metadata.PackageNotFoundError:
            out[n.lower()] = "NOT INSTALLED"
    return out


def git_state(root: Path) -> dict:
    def run(*a):
        return subprocess.run(
            ["git", *a], cwd=root, capture_output=True, text=True, timeout=20, check=False
        )

    head = run("rev-parse", "HEAD")
    if head.returncode != 0:
        return {"commit": None, "dirty": None}
    return {
        "commit": head.stdout.strip(),
        "dirty": bool(run("status", "--porcelain").stdout.strip()),
    }


def build_manifest(
    config: dict,
    seed: int,
    data_files: list[Path],
    code_dir: Path,
    tracked_packages: list[str],
    repo_root: Path | None = None,
) -> dict:
    m = {
        "python": sys.version.split()[0], "platform": platform.platform(), "machine": platform.machine(),
        "packages": packages(tracked_packages), "env": {k: os.environ.get(k) for k in RELEVANT_ENV},
        "seed": seed, "config": config, "config_hash": hashlib.sha256(canonical(config).encode()).hexdigest()[:16],
        "data": {str(p.name): sha256_file(p) for p in sorted(data_files)}, "code_hash": sha256_tree(code_dir),
        "git": git_state(repo_root) if repo_root else {"commit": None, "dirty": None},
    }  # fmt: skip
    ident = {k: m[k] for k in ("python", "packages", "seed", "config_hash", "data", "code_hash")}
    m["manifest_id"] = hashlib.sha256(canonical(ident).encode()).hexdigest()[:16]
    return m


def diff_manifests(old: dict, new: dict) -> list[dict]:
    """Differences ranked by how likely they are to change results."""
    out = []
    for key, sev in (
        ("data", "high"),
        ("code_hash", "high"),
        ("config_hash", "high"),
        ("seed", "high"),
        ("packages", "medium"),
        ("python", "medium"),
        ("env", "medium"),
        ("machine", "low"),
        ("platform", "low"),
    ):
        if old.get(key) != new.get(key):
            detail = None
            if isinstance(old.get(key), dict) and isinstance(new.get(key), dict):
                detail = sorted(
                    k for k in set(old[key]) | set(new[key]) if old[key].get(k) != new[key].get(k)
                )
            out.append({"field": key, "severity": sev, "changed": detail})
    if new.get("git", {}).get("dirty"):
        out.append(
            {
                "field": "git.dirty",
                "severity": "medium",
                "changed": ["working tree has uncommitted changes"],
            }
        )
    return out
