"""Real local-Spark tuning experiments (PySpark in local mode): shuffle partitions, AQE, parallelism, input file layout."""

from __future__ import annotations

import shutil
import statistics
import tempfile
import time
from pathlib import Path

import numpy as np
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window


def make_data(root: Path, n_orders: int, small_files: int = 200, seed: int = 0) -> dict[str, Path]:
    """Write orders as one parquet file and as many small files (same rows), plus a customers table."""
    rng = np.random.default_rng(seed)
    import pyarrow as pa
    import pyarrow.parquet as pq

    n_cust = 20_000
    orders = pa.table(
        {
            "order_id": np.arange(n_orders, dtype=np.int64),
            "customer_id": rng.integers(0, n_cust, n_orders),
            "amount": np.round(rng.gamma(2.0, 40.0, n_orders), 2),
            "month": rng.integers(1, 13, n_orders).astype(np.int32),
            "country": rng.choice(["DE", "FR", "US", "IN", "BR", "JP"], n_orders),
        }
    )
    cust = pa.table(
        {
            "customer_id": np.arange(n_cust, dtype=np.int64),
            "segment": rng.choice(["consumer", "smb", "enterprise"], n_cust),
        }
    )
    paths = {
        "one_file": root / "orders_one",
        "small_files": root / "orders_small",
        "customers": root / "customers",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    pq.write_table(orders, paths["one_file"] / "part-0.parquet")
    step = -(-n_orders // small_files)
    for i in range(small_files):
        pq.write_table(orders.slice(i * step, step), paths["small_files"] / f"part-{i:04d}.parquet")
    pq.write_table(cust, paths["customers"] / "part-0.parquet")
    return paths


def session(cores: int, shuffle_partitions: int, aqe: bool, memory: str = "3g") -> SparkSession:
    SparkSession.builder.getOrCreate().stop()  # fresh session: configs such as local[N] cannot change at runtime
    return (
        SparkSession.builder.master(f"local[{cores}]")
        .appName("emr-serverless-tuning")
        .config("spark.driver.memory", memory)
        .config("spark.sql.shuffle.partitions", shuffle_partitions)
        .config("spark.sql.adaptive.enabled", str(aqe).lower())
        .config("spark.sql.adaptive.coalescePartitions.enabled", str(aqe).lower())
        .config("spark.sql.adaptive.advisoryPartitionSizeInBytes", 8 * 1024 * 1024)
        .config("spark.sql.autoBroadcastJoinThreshold", 10 * 1024 * 1024)
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )


def job(spark: SparkSession, orders_path: Path, customers_path: Path) -> tuple[list[tuple], int]:
    """Join, aggregate and rank. Returns (sorted result rows, total tasks launched)."""
    sc = spark.sparkContext
    before = _task_total(sc)
    o, c = spark.read.parquet(str(orders_path)), spark.read.parquet(str(customers_path))
    j = o.join(F.broadcast(c), "customer_id")
    agg = j.groupBy("segment", "country", "month").agg(
        F.sum("amount").alias("revenue"), F.count("*").alias("orders")
    )
    per_cust = j.groupBy("segment", "customer_id").agg(F.sum("amount").alias("spend"))
    w = Window.partitionBy("segment").orderBy(F.desc("spend"))
    top = (
        per_cust.withColumn("rk", F.row_number().over(w))
        .filter("rk <= 3")
        .select("segment", "customer_id", "spend", "rk")
    )
    rows = [
        ("agg", r.segment, r.country, r.month, round(r.revenue, 2), r.orders) for r in agg.collect()
    ] + [("top", r.segment, r.customer_id, round(r.spend, 2), r.rk) for r in top.collect()]
    return sorted(rows, key=str), _task_total(sc) - before


def _task_total(sc) -> int:
    tr = sc.statusTracker()
    total = 0
    for s in tr.getActiveStageIds() + _all_stage_ids(sc):
        info = tr.getStageInfo(s)
        total += info.numTasks if info else 0
    return total


def _all_stage_ids(sc) -> list[int]:
    tr = sc.statusTracker()
    ids = set()
    for j in tr.getJobIdsForGroup(None) or []:
        ji = tr.getJobInfo(j)
        if ji:
            ids.update(ji.stageIds)
    return sorted(ids)


def timed(spark, orders: Path, customers: Path, repeats: int) -> dict:
    job(spark, orders, customers)  # warm-up (JIT, file listing)
    times, tasks, digest = [], 0, None
    for _ in range(repeats):
        t0 = time.perf_counter()
        rows, tasks = job(spark, orders, customers)
        times.append(time.perf_counter() - t0)
        digest = hash(tuple(rows))
    return {"runtime_s": round(statistics.median(times), 3), "tasks": tasks, "digest": digest}


def run_all(n_orders: int = 1_500_000, repeats: int = 3) -> dict:
    out: dict = {"n_orders": n_orders, "experiments": []}
    with tempfile.TemporaryDirectory() as t:
        paths = make_data(Path(t), n_orders)
        grid = []
        for sp in (1, 4, 16, 64, 400):
            grid.append(("shuffle partitions (AQE off)", f"{sp}", 4, sp, False, "one_file"))
        grid.append(("AQE coalescing", "400 initial, AQE on", 4, 400, True, "one_file"))
        for cores in (1, 2, 4):
            grid.append(("parallelism", f"local[{cores}]", cores, 16, False, "one_file"))
        grid.append(("input layout", "1 file", 4, 16, False, "one_file"))
        grid.append(("input layout", "200 small files", 4, 16, False, "small_files"))
        digests = set()
        for group, label, cores, sp, aqe, layout in grid:
            spark = session(cores, sp, aqe)
            m = timed(spark, paths[layout], paths["customers"], repeats)
            digests.add(m.pop("digest"))
            out["experiments"].append({"group": group, "config": label, "workers_vcpu": cores, **m})
        SparkSession.builder.getOrCreate().stop()
        out["all_configs_same_result"] = len(digests) == 1
    shutil.rmtree("spark-warehouse", ignore_errors=True)
    return out
