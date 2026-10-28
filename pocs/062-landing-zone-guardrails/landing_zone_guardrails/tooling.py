"""Run a local moto server and apply the Terraform module to it. Never contacts real AWS."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

POC = Path(__file__).resolve().parent.parent


class TerraformError(RuntimeError):
    pass


def terraform_available() -> bool:
    return shutil.which("terraform") is not None


def moto_available() -> bool:
    try:
        import boto3  # noqa: F401
        import moto  # noqa: F401
    except ImportError:
        return False
    return (
        shutil.which("moto_server") is not None
        or Path(sys.executable).with_name("moto_server").exists()
    )


def _env() -> dict[str, str]:
    env = dict(os.environ)
    cache = Path(tempfile.gettempdir()) / "tf-plugin-cache"
    cache.mkdir(exist_ok=True)
    env.update(
        {
            "TF_IN_AUTOMATION": "1",
            "TF_INPUT": "0",
            "TF_PLUGIN_CACHE_DIR": str(cache),
            "AWS_ACCESS_KEY_ID": "mock",
            "AWS_SECRET_ACCESS_KEY": "mock",
            "AWS_EC2_METADATA_DISABLED": "true",
        }
    )
    for k in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE"):
        env.pop(k, None)  # a developer's real credentials must never reach these runs
    return env


def tf(
    args: list[str], cwd: Path, *, check: bool = True, timeout: int = 600
) -> subprocess.CompletedProcess:
    p = subprocess.run(
        ["terraform", *args],
        cwd=cwd,
        env=_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and p.returncode != 0:
        raise TerraformError(
            f"terraform {' '.join(args)} failed:\n{p.stdout[-1200:]}\n{p.stderr[-1200:]}"
        )
    return p


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def moto_server():
    port = free_port()
    exe = shutil.which("moto_server") or str(Path(sys.executable).with_name("moto_server"))
    proc = subprocess.Popen(
        [exe, "-H", "127.0.0.1", "-p", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/moto-api/", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("moto server did not start")
        yield port
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def org_client(port: int):
    import boto3

    return boto3.client(
        "organizations",
        region_name="us-east-1",
        endpoint_url=f"http://127.0.0.1:{port}",
        aws_access_key_id="mock",
        aws_secret_access_key="mock",
    )


def workspace(dest: Path, policy_dir: Path | None = None, org_json: Path | None = None) -> Path:
    """Copy infra/, org.json and policies/ so runs never dirty the repository; returns the infra directory."""
    shutil.copytree(
        POC / "infra",
        dest / "infra",
        ignore=shutil.ignore_patterns(".terraform*", "*.tfstate*", "*.tfplan"),
    )
    shutil.copytree(policy_dir or POC / "policies", dest / "policies")
    shutil.copy(org_json or POC / "org.json", dest / "org.json")
    return dest / "infra"


def read_back(port: int) -> dict:
    """What actually exists in the (mock) organisation after apply: policies with content and per-target attachments."""
    c = org_client(port)
    pols = {}
    for p in c.list_policies(Filter="SERVICE_CONTROL_POLICY")["Policies"]:
        body = c.describe_policy(PolicyId=p["Id"])["Policy"]
        pols[p["Name"]] = {"id": p["Id"], "content": json.loads(body["Content"])}
    root = c.list_roots()["Roots"][0]["Id"]
    targets = {
        "root": root,
        **{
            o["Name"]: o["Id"]
            for o in c.list_organizational_units_for_parent(ParentId=root)["OrganizationalUnits"]
        },
    }
    att = {
        n: sorted(
            x["Name"]
            for x in c.list_policies_for_target(TargetId=t, Filter="SERVICE_CONTROL_POLICY")[
                "Policies"
            ]
        )
        for n, t in targets.items()
    }
    return {"policies": pols, "attachments": att}


def reconcile_defaults(port: int, cfg: dict) -> list[str]:
    """Detach the default FullAWSAccess policy from OUs whose desired policy set does not include it.

    AWS attaches FullAWSAccess to every new OU, so an allow-list SCP is silently ineffective until it is detached.
    Returns the OU names that were fixed.
    """
    c = org_client(port)
    root = c.list_roots()["Roots"][0]["Id"]
    ous = {
        o["Name"]: o["Id"]
        for o in c.list_organizational_units_for_parent(ParentId=root)["OrganizationalUnits"]
    }
    fixed = []
    for name, ou in cfg["root"]["ous"].items():
        if "FullAWSAccess" not in ou["policies"]:
            attached = {
                p["Name"]: p["Id"]
                for p in c.list_policies_for_target(
                    TargetId=ous[name], Filter="SERVICE_CONTROL_POLICY"
                )["Policies"]
            }
            if "FullAWSAccess" in attached:
                c.detach_policy(PolicyId=attached["FullAWSAccess"], TargetId=ous[name])
                fixed.append(name)
    return fixed
