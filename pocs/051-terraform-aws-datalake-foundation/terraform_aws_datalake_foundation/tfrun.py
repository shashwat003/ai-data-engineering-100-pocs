"""Thin wrappers around the terraform CLI. Nothing here ever runs `apply` against real AWS."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

INFRA = Path(__file__).resolve().parent.parent / "infra"
PLUGIN_CACHE = Path(tempfile.gettempdir()) / "tf-plugin-cache"


class TerraformError(RuntimeError):
    pass


def available() -> bool:
    return shutil.which("terraform") is not None


def version() -> str:
    out = subprocess.run(
        ["terraform", "version", "-json"], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)["terraform_version"]


def _env() -> dict[str, str]:
    PLUGIN_CACHE.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.update({"TF_IN_AUTOMATION": "1", "TF_INPUT": "0", "TF_PLUGIN_CACHE_DIR": str(PLUGIN_CACHE),
                "AWS_ACCESS_KEY_ID": "mock", "AWS_SECRET_ACCESS_KEY": "mock",
                "AWS_EC2_METADATA_DISABLED": "true"})  # fmt: skip
    for k in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE"):
        env.pop(k, None)  # a developer's real credentials must never leak into these runs
    return env


def tf(args: list[str], cwd: Path, *, check: bool = True, timeout: int = 600):
    proc = subprocess.run(["terraform", *args], cwd=cwd, env=_env(), capture_output=True, text=True,
                          timeout=timeout)  # fmt: skip
    if check and proc.returncode != 0:
        raise TerraformError(
            f"terraform {' '.join(args)} failed:\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
        )
    return proc


def workspace(dest: Path) -> Path:
    """Copy infra/ (without caches or state) into dest so runs never dirty the repository."""
    shutil.copytree(
        INFRA,
        dest / "infra",
        ignore=shutil.ignore_patterns(".terraform*", "*.tfstate*", "*.tfplan"),
    )
    return dest / "infra"


def init(d: Path) -> None:
    tf(["init", "-backend=false", "-no-color"], d)


def plan_json(d: Path, *var_args: str) -> dict:
    plan = d / "plan.tfplan"
    tf(["plan", "-no-color", "-lock=false", f"-out={plan}", *var_args], d)
    return json.loads(tf(["show", "-json", str(plan)], d).stdout)


def state_json(d: Path) -> dict:
    return json.loads(tf(["show", "-json", "-no-color"], d).stdout)
