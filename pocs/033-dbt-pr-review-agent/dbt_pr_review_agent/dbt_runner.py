"""Run real dbt (dbt-core + dbt-duckdb) on a project copy and fingerprint the resulting models."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

from .scenarios import BASE, SCENARIOS


def make_head(name: str, dest: Path) -> Path:
    shutil.copytree(BASE, dest, dirs_exist_ok=True)
    for rel, content in SCENARIOS[name].items():
        p = dest / rel
        if content is None:
            p.unlink(missing_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    return dest


def dbt_build(project: Path, work: Path) -> dict:
    db = work / "wh.duckdb"
    env = {
        **os.environ,
        "DBT_DB_PATH": str(db),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        "DO_NOT_TRACK": "1",
    }
    exe = str(Path(sys.executable).with_name("dbt"))
    p = subprocess.run(
        [
            exe,
            "build",
            "--project-dir",
            str(project),
            "--profiles-dir",
            str(project),
            "--target-path",
            str(work / "target"),
            "--log-path",
            str(work / "logs"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    fp: dict[str, tuple] = {}
    if db.exists():
        con = duckdb.connect(str(db), read_only=True)
        try:
            for (t,) in con.execute(
                "select table_name from information_schema.tables where table_schema='main'"
            ).fetchall():
                if t.startswith(("stg_", "dim_", "fct_", "mart_")):
                    n, chk = con.execute(
                        f'select count(*), coalesce(sum(hash(t::varchar) % 1000003), 0) from "{t}" t'
                    ).fetchone()  # noqa: S608
                    fp[t] = (n, int(chk))
        finally:
            con.close()
    tail = [ln for ln in p.stdout.splitlines() if "ERROR" in ln or "FAIL" in ln][:3]
    return {"ok": p.returncode == 0, "fingerprint": fp, "errors": [ln.strip()[:160] for ln in tail]}


def ground_truth(name: str, base_fp: dict) -> dict:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        head = make_head(name, t / "proj")
        res = dbt_build(head, t)
    changed = sorted(m for m, v in res["fingerprint"].items() if m in base_fp and v != base_fp[m])
    return {
        "dbt_ok": res["ok"],
        "data_changed_models": changed,
        "errors": res["errors"],
        "risky": (not res["ok"]) or bool(changed),
    }
