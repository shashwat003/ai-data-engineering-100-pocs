"""Join strategies for a skewed fact table joined to a dimension. All return the same rows.

baseline        sort-merge join, AQE off (what a default pre-3.0 style job does)
aqe             sort-merge join with AQE skew-join handling (Spark splits the big partition)
broadcast       broadcast the dimension: no shuffle of the fact table at all
salt_hot        selective salting: split only the heavy-hitter keys, replicate only their dim rows
salt_all        naive salting: every key salted, whole dimension replicated n_salt times
"""

from __future__ import annotations

import math

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

SALT = "_salt"


def configure(
    spark: SparkSession, variant: str, partitions: int, skew_bytes: int = 2 * 1024 * 1024
) -> None:
    """Runtime SQL settings per variant (AQE and broadcast are session-level switches)."""
    aqe = variant == "aqe"
    spark.conf.set("spark.sql.adaptive.enabled", str(aqe).lower())
    spark.conf.set("spark.sql.adaptive.skewJoin.enabled", str(aqe).lower())
    spark.conf.set("spark.sql.adaptive.coalescePartitions.enabled", "false")
    spark.conf.set("spark.sql.autoBroadcastJoinThreshold", "-1")  # broadcast only when hinted
    spark.conf.set("spark.sql.shuffle.partitions", str(partitions))
    if aqe:  # defaults assume 256 MB partitions; this dataset is ~100x smaller
        spark.conf.set(
            "spark.sql.adaptive.skewJoin.skewedPartitionThresholdInBytes", str(skew_bytes)
        )
        spark.conf.set("spark.sql.adaptive.skewJoin.skewedPartitionFactor", "3")
        spark.conf.set("spark.sql.adaptive.advisoryPartitionSizeInBytes", str(skew_bytes))


def choose_salts(hot_share: float, partitions: int) -> int:
    """Split the hottest key into about one average partition per piece."""
    return max(2, min(partitions, math.ceil(hot_share * partitions)))


def join_baseline(events: DataFrame, dim: DataFrame, key: str) -> DataFrame:
    return events.join(dim, key)


def join_broadcast(events: DataFrame, dim: DataFrame, key: str) -> DataFrame:
    return events.join(F.broadcast(dim), key)


def join_salted(events: DataFrame, dim: DataFrame, key: str, *, n_salt: int,
                hot_keys: list | None, spark: SparkSession) -> DataFrame:  # fmt: skip
    """Salt `hot_keys` (or every key if None). The salt is derived from event_id, so it is
    deterministic: recomputing a task after a failure puts each row in the same place.

    Fact side: hot rows get salt in [0, n_salt), other rows salt 0.
    Dimension side: hot keys are replicated once per salt value; other keys stay single.
    """
    is_hot = F.lit(True) if hot_keys is None else F.col(key).isin(hot_keys)
    salted_events = events.withColumn(
        SALT, F.when(is_hot, F.pmod(F.hash("event_id"), F.lit(n_salt))).otherwise(F.lit(0))
    )
    salts = spark.range(n_salt).select(F.col("id").cast("int").alias(SALT))
    dim_hot = dim.filter(is_hot).crossJoin(salts)
    dim_cold = dim.filter(~is_hot).withColumn(SALT, F.lit(0))
    return salted_events.join(dim_hot.unionByName(dim_cold), [key, SALT]).drop(SALT)


def dim_rows_after_salting(n_dim: int, n_hot: int, n_salt: int) -> int:
    """Cost of salting on the dimension side: the replication is the price of balance."""
    return n_dim + n_hot * (n_salt - 1)


def workload(joined: DataFrame) -> DataFrame:
    """Per-row CPU work after the join (a hash of the payload), then a tiny aggregation.

    The hash stands in for real per-row enrichment (parsing, scoring, masking): it makes the cost
    of a partition proportional to its row count, which is exactly when skew hurts.
    """
    h = F.length(F.sha2(F.concat_ws("|", F.col("event_id").cast("string"), F.col("payload")), 256))
    return (joined.groupBy("country", "segment")
            .agg(F.count("*").alias("events"), F.sum("amount").alias("amount"), F.sum(h).alias("hash_work"))
            .orderBy("country", "segment"))  # fmt: skip
