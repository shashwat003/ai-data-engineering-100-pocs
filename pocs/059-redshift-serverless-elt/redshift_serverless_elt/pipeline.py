"""Run the dbt project (real dbt-core + DuckDB adapter) against the local warehouse and collect results."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

POC_DIR = Path(__file__).resolve().parent.parent
PROJECT = POC_DIR / "dbt_project"
INFRA = POC_DIR / "infra"


def dbt(command: list[str], work: Path, db: Path, *, extra: list[str] | None = None) -> dict:
    """Run dbt in a fresh process (in-process runs share adapter state across databases) and summarise run_results.json."""
    env = {
        **os.environ,
        "ELT_DB": str(db),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        "DO_NOT_TRACK": "1",
    }
    target = work / "target"
    args = [sys.executable, "-c", "from dbt.cli.main import cli; cli()", "--quiet", "--no-use-colors", *command,
            "--project-dir", str(PROJECT), "--profiles-dir", str(PROJECT), "--target-path", str(target), "--log-path", str(work / "logs"), *(extra or [])]  # fmt: skip
    (target).mkdir(parents=True, exist_ok=True)
    (target / "run_results.json").unlink(missing_ok=True)
    p = subprocess.run(args, env=env, capture_output=True, text=True, timeout=600, check=False)
    by: dict[str, int] = {}
    rr = target / "run_results.json"
    if rr.exists():
        for r in json.loads(rr.read_text())["results"]:
            by[r["status"]] = by.get(r["status"], 0) + 1
    return {"success": p.returncode == 0, "nodes": sum(by.values()), "by_status": by}


def columns(db: Path, schema: str = "main") -> dict[str, list[str]]:
    con = duckdb.connect(str(db))
    rows = con.execute(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = ? ORDER BY ordinal_position",
        [schema],
    ).fetchall()
    con.close()
    out: dict[str, list[str]] = {}
    for t, c in rows:
        out.setdefault(t, []).append(c)
    return out


def terraform_plan(extra: list[str] | None = None) -> dict | None:
    """`terraform plan` with mock credentials, offline, nothing applied. None when terraform is not installed."""
    if shutil.which("terraform") is None:
        return None
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
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
    )  # noqa: S106
    with tempfile.TemporaryDirectory() as t:
        d = Path(t) / "infra"
        shutil.copytree(
            INFRA, d, ignore=shutil.ignore_patterns(".terraform*", "*.tfstate*", "*.tfplan")
        )
        for args in (
            ["init", "-backend=false", "-no-color"],
            ["plan", "-no-color", "-lock=false", "-out=p.tfplan", *(extra or [])],
        ):
            p = subprocess.run(
                ["terraform", *args],
                cwd=d,
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            if p.returncode:
                raise RuntimeError(
                    f"terraform {args[0]} failed:\n{p.stdout[-800:]}\n{p.stderr[-800:]}"
                )
        out = subprocess.run(
            ["terraform", "show", "-json", "p.tfplan"],
            cwd=d,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(out.stdout)
