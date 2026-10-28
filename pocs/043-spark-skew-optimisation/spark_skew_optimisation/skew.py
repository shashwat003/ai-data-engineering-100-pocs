"""Measure skew before fixing it: heavy hitters and the shape of the post-shuffle partitions."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from pyspark.sql import DataFrame
from pyspark.sql import functions as F


@dataclass(frozen=True)
class PartitionStats:
    partitions: int
    rows: int
    max: int
    median: float
    mean: float
    max_over_median: float
    cv: float  # coefficient of variation of partition sizes
    largest_share: float  # fraction of all rows in the largest partition

    def as_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def stats_from_counts(counts: list[int], total_partitions: int | None = None) -> PartitionStats:
    """Empty partitions count too: a skewed shuffle leaves many partitions nearly idle."""
    n = total_partitions or len(counts)
    sizes = [*counts, *([0] * (n - len(counts)))]
    rows = sum(sizes)
    mean = rows / n
    median = statistics.median(sizes)
    sd = math.sqrt(sum((s - mean) ** 2 for s in sizes) / n)
    return PartitionStats(n, rows, max(sizes), median, mean,
                          max(sizes) / median if median else float("inf"),
                          sd / mean if mean else 0.0, max(sizes) / rows if rows else 0.0)  # fmt: skip


def shuffle_partition_stats(df: DataFrame, partitions: int, *cols: str) -> PartitionStats:
    """Row counts per partition if `df` were hash-partitioned by `cols` into `partitions`."""
    rows = (
        df.repartition(partitions, *cols)
        .groupBy(F.spark_partition_id().alias("p"))
        .count()
        .collect()
    )
    return stats_from_counts([r["count"] for r in rows], partitions)


def heavy_hitters(df: DataFrame, key: str, *, partitions: int, sample_fraction: float = 0.02,
                  factor: float = 2.0, seed: int = 11) -> list[dict]:  # fmt: skip
    """Keys whose expected shuffle partition would hold more than `factor` x an even share.

    Uses a sample, so it costs one cheap pass; keys near the threshold may be missed or included
    by chance, which is fine for choosing what to salt.
    """
    sample = df.sample(fraction=sample_fraction, seed=seed)
    total = sample.count()
    threshold = factor * total / partitions
    rows = (
        sample.groupBy(key)
        .count()
        .filter(F.col("count") > threshold)
        .orderBy(F.desc("count"))
        .collect()
    )
    return [
        {"key": r[key], "sample_rows": r["count"], "share": round(r["count"] / total, 4)}
        for r in rows
    ]
