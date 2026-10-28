"""Run a local moto server (a mock of AWS APIs) and apply the module to it. Never touches real AWS."""

from __future__ import annotations

import contextlib
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .tfrun import tf

REGION = "eu-west-1"


def VARS(port: int) -> list[str]:  # noqa: N802
    """moto lacks Budgets subscriber APIs, so the applied stack omits the budget (plans keep it)."""
    return ["-var", f"mock_port={port}", "-var", "apply_budget=false"]


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


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def moto_server():
    port = free_port()
    exe = shutil.which("moto_server") or str(Path(sys.executable).with_name("moto_server"))
    proc = subprocess.Popen([exe, "-H", "127.0.0.1", "-p", str(port)], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)  # fmt: skip
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


def client(service: str, port: int):
    import boto3
    from botocore.config import Config

    return boto3.client(service, region_name=REGION, endpoint_url=f"http://127.0.0.1:{port}",
                        aws_access_key_id="mock", aws_secret_access_key="mock",
                        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 1}))  # fmt: skip


def apply(d: Path, port: int) -> None:
    tf(["apply", "-auto-approve", "-no-color", "-lock=false", *VARS(port)], d, timeout=900)


def empty_buckets(port: int) -> int:
    """Delete every object version (after removing bucket policies). The module sets force_destroy=false on purpose, so `destroy`
    refuses to delete non-empty buckets (the mock writes access-log objects, as AWS would)."""
    s3, n = client("s3", port), 0
    for b in s3.list_buckets()["Buckets"]:
        s3.delete_bucket_policy(
            Bucket=b["Name"]
        )  # moto applies Deny statements without their conditions
        for page in s3.get_paginator("list_object_versions").paginate(Bucket=b["Name"]):
            for v in [*page.get("Versions", []), *page.get("DeleteMarkers", [])]:
                s3.delete_object(Bucket=b["Name"], Key=v["Key"], VersionId=v["VersionId"])
                n += 1
    return n


def destroy(d: Path, port: int, attempts: int = 3) -> None:
    """Destroy, emptying buckets between attempts: access-log objects keep arriving while the
    stack is torn down (in the mock and in AWS), and force_destroy is deliberately false."""
    for attempt in range(attempts):
        empty_buckets(port)
        proc = tf(["destroy", "-auto-approve", "-no-color", "-lock=false", *VARS(port)], d,
                  check=attempt == attempts - 1, timeout=900)  # fmt: skip
        if proc.returncode == 0:
            return


def drift(d: Path, port: int) -> int:
    """Exit code of a refresh-and-plan: 0 = no changes, 2 = changes pending."""
    return tf(["plan", "-detailed-exitcode", "-no-color", "-lock=false", *VARS(port)], d,
              check=False).returncode  # fmt: skip
