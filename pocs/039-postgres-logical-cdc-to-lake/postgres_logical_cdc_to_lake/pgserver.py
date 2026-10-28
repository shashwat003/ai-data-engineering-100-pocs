"""Start a throw-away local PostgreSQL server with logical decoding enabled (no Docker, no network).

Uses the server binaries already installed on the machine (/usr/lib/postgresql/*/bin or PATH). PostgreSQL refuses to run as
root, so when started as root the server runs as the unprivileged `nobody` user through setpriv.
"""

from __future__ import annotations

import glob
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import psycopg


def find_bin() -> Path | None:
    cands = sorted(glob.glob("/usr/lib/postgresql/*/bin/postgres"), reverse=True)
    if cands:
        return Path(cands[0]).parent
    which = shutil.which("postgres")
    return Path(which).parent if which else None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PgServer:
    def __init__(self, base: str | Path | None = None, max_wal_gb: int = 1):
        self.bin = find_bin()
        if self.bin is None:
            raise RuntimeError("no PostgreSQL server binaries found")
        self.base = Path(base) if base else Path(tempfile.mkdtemp(prefix="pgcdc-"))
        self.base.mkdir(parents=True, exist_ok=True)
        self.as_root = os.geteuid() == 0
        if self.as_root:
            os.chown(self.base, 65534, 65534)
        self.data = self.base / "data"
        self.port = free_port()
        self.max_wal_gb = max_wal_gb

    def _run(self, *cmd: str) -> subprocess.CompletedProcess:
        prefix = (
            ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups"] if self.as_root else []
        )
        env = {**os.environ, "LC_ALL": "C", "HOME": str(self.base)}
        return subprocess.run([*prefix, *cmd], capture_output=True, text=True, env=env, check=False)

    def start(self) -> PgServer:
        r = self._run(
            str(self.bin / "initdb"), "-D", str(self.data), "-A", "trust", "-U", "cdc", "-E", "UTF8"
        )
        if r.returncode:
            raise RuntimeError(f"initdb failed: {r.stderr[-300:]}")
        opts = (
            f"-p {self.port} -k {self.base} -c listen_addresses='' -c wal_level=logical "
            f"-c max_replication_slots=4 -c max_wal_senders=4 -c fsync=off "
            f"-c max_slot_wal_keep_size={self.max_wal_gb}GB"
        )
        r = self._run(
            str(self.bin / "pg_ctl"),
            "-D",
            str(self.data),
            "-o",
            opts,
            "-l",
            str(self.base / "log"),
            "-w",
            "start",
        )
        if r.returncode:
            raise RuntimeError(f"pg_ctl start failed: {r.stdout[-300:]} {r.stderr[-300:]}")
        return self

    def stop(self) -> None:
        self._run(str(self.bin / "pg_ctl"), "-D", str(self.data), "-m", "immediate", "stop")

    def connect(self, dbname: str = "postgres", **kw) -> psycopg.Connection:
        return psycopg.connect(host=str(self.base), port=self.port, user="cdc", dbname=dbname, **kw)

    def __enter__(self) -> PgServer:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
        shutil.rmtree(self.base, ignore_errors=True)
