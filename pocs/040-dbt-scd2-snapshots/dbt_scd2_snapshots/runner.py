"""Drive a real dbt project (dbt-core + dbt-duckdb) through several 'days' of source changes."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

POC_DIR = Path(__file__).resolve().parent.parent
PROJECT = POC_DIR / "dbt_project"
DAY0 = datetime(2025, 1, 1)
FAR = datetime(9999, 12, 31)


class Warehouse:
    def __init__(self, workdir: Path):
        self.dir = workdir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "wh.duckdb"
        con = duckdb.connect(str(self.db_path))
        con.execute("CREATE SCHEMA IF NOT EXISTS raw")
        con.execute(
            "CREATE OR REPLACE TABLE raw.customers (customer_id INTEGER, name VARCHAR, tier VARCHAR, country VARCHAR, last_login TIMESTAMP, updated_at TIMESTAMP)"
        )
        con.close()
        self.env = {**os.environ, "SCD2_DB_PATH": str(self.db_path), "DBT_PROFILES_DIR": str(PROJECT),
                    "DBT_SEND_ANONYMOUS_USAGE_STATS": "false", "DO_NOT_TRACK": "1"}  # fmt: skip
        self.log: list[
            tuple[int, str]
        ] = []  # (day, 'insert/update/delete ...') ground truth change log

    def sql(self, q: str, *args):
        con = duckdb.connect(str(self.db_path))
        try:
            return con.execute(q, list(args)).fetchall()
        finally:
            con.close()

    def day(self, n: int) -> datetime:
        return DAY0 + timedelta(days=n)

    def upsert(
        self, day: int, cid: int, name: str, tier: str, country: str, touch_login_only: bool = False
    ):
        ts = self.day(day)
        if self.sql("SELECT 1 FROM raw.customers WHERE customer_id=?", cid):
            if touch_login_only:  # untracked column changes and updated_at moves
                self.sql(
                    "UPDATE raw.customers SET last_login=?, updated_at=? WHERE customer_id=?",
                    ts,
                    ts,
                    cid,
                )
            else:
                self.sql(
                    "UPDATE raw.customers SET name=?, tier=?, country=?, last_login=?, updated_at=? WHERE customer_id=?",
                    name,
                    tier,
                    country,
                    ts,
                    ts,
                    cid,
                )
        else:
            self.sql(
                "INSERT INTO raw.customers VALUES (?,?,?,?,?,?)", cid, name, tier, country, ts, ts
            )

    def delete(self, cid: int):
        self.sql("DELETE FROM raw.customers WHERE customer_id=?", cid)

    def dbt(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        # dbt stamps check-strategy versions with the wall clock, so "days" are simulated by run order there
        # and by the source's updated_at for the timestamp strategy.
        exe = str(Path(sys.executable).with_name("dbt"))
        p = subprocess.run(
            [
                exe,
                *args,
                "--project-dir",
                str(PROJECT),
                "--profiles-dir",
                str(PROJECT),
                "--target-path",
                str(self.dir / "target"),
            ],
            env=self.env,
            capture_output=True,
            text=True,
            cwd=PROJECT,
        )
        if check and p.returncode != 0:
            raise RuntimeError(
                f"dbt {' '.join(args)} failed:\n{p.stdout[-1500:]}\n{p.stderr[-500:]}"
            )
        return p

    def snapshot(self):
        return self.dbt("snapshot")

    def versions(self, table: str) -> list[tuple]:
        return self.sql(
            f"SELECT customer_id, tier, country, dbt_valid_from IS NOT NULL, dbt_valid_to FROM main_snapshots.{table} ORDER BY customer_id, dbt_valid_from"
        )

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def scenario(w: Warehouse) -> None:
    """Five simulated days of source changes, with a dbt snapshot run after each day."""
    w.upsert(0, 1, "Ann", "free", "US")
    w.upsert(0, 2, "Bob", "pro", "DE")
    w.upsert(0, 3, "Cy", "free", "IN")  # noqa: E702
    w.snapshot()
    w.upsert(1, 1, "Ann", "pro", "US")
    w.upsert(1, 2, "Bob", "pro", "DE", touch_login_only=True)  # noqa: E702
    w.snapshot()
    w.snapshot()  # day 2: nothing changed; a re-run must write nothing
    w.delete(3)
    w.upsert(3, 4, "Dee", "enterprise", "BR")
    w.upsert(3, 1, "Ann", "pro", "GB")  # noqa: E702
    w.snapshot()
    w.upsert(4, 1, "Anne", "pro", "GB")
    w.upsert(4, 3, "Cy", "free", "IN")  # name-only change; Cy resurrected  # noqa: E702
    w.snapshot()
