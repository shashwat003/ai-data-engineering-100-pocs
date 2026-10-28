"""Seeded synthetic event and customer data with a controllable hot key.

`customer_id = 0` models the "anonymous / unknown customer" bucket that many real pipelines
default to; it receives `hot_share` of all events. The remaining events follow a power-law over the
other customers (cubed uniform), so there is also a moderately skewed tail.
"""

from __future__ import annotations

from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


def generate(
    spark: SparkSession,
    out_dir: str | Path,
    *,
    events: int = 4_000_000,
    customers: int = 200_000,
    hot_share: float = 0.5,
    seed: int = 7,
) -> dict:
    out = Path(out_dir)
    ev = spark.range(events).select(
        F.col("id").alias("event_id"),
        F.when(F.rand(seed) < hot_share, F.lit(0))
        .otherwise((F.pow(F.rand(seed + 1), 3) * (customers - 1)).cast("long") + 1)
        .alias("customer_id"),
        (F.rand(seed + 2) * 100).cast("decimal(10,2)").alias("amount"),
        F.concat(F.lit("evt-"), F.md5(F.col("id").cast("string"))).alias("payload"),
    )
    ev.repartition(8).write.mode("overwrite").parquet(str(out / "events"))
    cu = spark.range(customers).select(
        F.col("id").alias("customer_id"),
        F.element_at(F.array(*[F.lit(s) for s in ("consumer", "smb", "enterprise", "public", "partner")]), (F.col("id") % 5 + 1).cast("int")).alias("segment"),
        F.element_at(F.array(*[F.lit(c) for c in ("US", "DE", "IN", "BR", "JP", "GB", "CA")]), (F.col("id") % 7 + 1).cast("int")).alias("country"),
    )  # fmt: skip
    cu.coalesce(1).write.mode("overwrite").parquet(str(out / "customers"))
    return manifest(spark, out)


def _bytes(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*.parquet"))


def manifest(spark: SparkSession, out: Path) -> dict:
    e, c = spark.read.parquet(str(out / "events")), spark.read.parquet(str(out / "customers"))
    hot = e.filter("customer_id = 0").count()
    n = e.count()
    return {"events": n, "customers": c.count(), "hot_key_rows": hot, "hot_key_share": round(hot / n, 4),
            "events_parquet_mb": round(_bytes(out / "events") / 2**20, 1),
            "customers_parquet_mb": round(_bytes(out / "customers") / 2**20, 2)}  # fmt: skip
