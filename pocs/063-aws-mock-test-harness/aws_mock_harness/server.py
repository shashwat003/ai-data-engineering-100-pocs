"""A local moto server plus boto3 factories: one process per pytest session, state reset between tests."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REGION = "eu-west-1"
CREDS = {
    "aws_access_key_id": "mock",
    "aws_secret_access_key": "mock",
}  # literal strings: never real credentials


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _exe() -> str:
    found = shutil.which("moto_server")
    return found or str(Path(sys.executable).with_name("moto_server"))


class MockAws:
    """Handle to a running local AWS mock. Use as a context manager or call start()/stop()."""

    def __init__(self, port: int | None = None, startup_timeout: float = 20.0):
        self.port = port or free_port()
        self.timeout = startup_timeout
        self.proc: subprocess.Popen | None = None
        self.startup_seconds = 0.0

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> MockAws:
        t0 = time.perf_counter()
        self.proc = subprocess.Popen(
            [_exe(), "-H", "127.0.0.1", "-p", str(self.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("moto server exited during startup")
            try:
                urllib.request.urlopen(f"{self.endpoint}/moto-api/", timeout=1).read()
                self.startup_seconds = time.perf_counter() - t0
                return self
            except OSError:
                time.sleep(0.1)
        self.stop()
        raise RuntimeError(f"moto server did not become ready within {self.timeout}s")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def __enter__(self) -> MockAws:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def reset(self) -> None:
        """Drop all state (buckets, queues, tables, ...) in O(ms): the isolation primitive between tests."""
        req = urllib.request.Request(f"{self.endpoint}/moto-api/reset", method="POST")
        urllib.request.urlopen(req, timeout=5).read()

    def client(self, service: str, region: str = REGION):
        import boto3

        return boto3.client(service, region_name=region, endpoint_url=self.endpoint, **CREDS)

    def resource(self, service: str, region: str = REGION):
        import boto3

        return boto3.resource(service, region_name=region, endpoint_url=self.endpoint, **CREDS)

    def env(self) -> dict[str, str]:
        """Environment for a subprocess (Terraform, a CLI, an app under test): points the AWS SDKs here, strips real credentials."""
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("AWS_") or k in ("AWS_EC2_METADATA_DISABLED",)
        }
        env.update(
            {
                "AWS_ENDPOINT_URL": self.endpoint,
                "AWS_ACCESS_KEY_ID": "mock",
                "AWS_SECRET_ACCESS_KEY": "mock",
                "AWS_DEFAULT_REGION": REGION,
                "AWS_EC2_METADATA_DISABLED": "true",
            }
        )
        return env
