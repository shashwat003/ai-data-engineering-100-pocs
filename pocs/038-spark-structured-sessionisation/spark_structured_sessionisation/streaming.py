"""Spark Structured Streaming sessionisation with session_window and a watermark."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType, TimestampType

SCHEMA = StructType(
    [
        StructField("user", StringType()),
        StructField("ts", TimestampType()),
        StructField("page", StringType()),
    ]
)


def get_spark(cores: int = 2) -> SparkSession:
    return (
        SparkSession.builder.master(f"local[{cores}]").appName("sessionisation")
        .config("spark.sql.shuffle.partitions", "4").config("spark.ui.enabled", "false").config("spark.ui.showConsoleProgress", "false")
        .config("spark.sql.session.timeZone", "UTC").config("spark.driver.memory", "1g").getOrCreate()
    )  # fmt: skip


def run_stream(
    spark: SparkSession,
    input_dir: Path,
    checkpoint: Path,
    gap: str = "30 minutes",
    watermark: str = "30 minutes",
) -> list[dict]:
    """Process every file in order (one micro-batch per file); return emitted sessions with their batch id."""
    emitted: list[dict] = []

    def sink(df, batch_id):
        for r in df.collect():
            emitted.append(
                {
                    "batch": batch_id,
                    "user": r["user"],
                    "start": r["start"],
                    "end": r["end"],
                    "count": r["n"],
                }
            )

    events = (
        spark.readStream.schema(SCHEMA)
        .option("maxFilesPerTrigger", 1)
        .option("latestFirst", "false")
        .json(str(input_dir))
    )
    sessions = (
        events.withWatermark("ts", watermark)
        .groupBy(F.col("user"), F.session_window("ts", gap))
        .agg(F.count("*").alias("n"))
        .select(
            "user",
            F.col("session_window.start").alias("start"),
            F.col("session_window.end").alias("end"),
            "n",
        )
    )
    q = (
        sessions.writeStream.outputMode("append")
        .foreachBatch(sink)
        .option("checkpointLocation", str(checkpoint))
        .trigger(availableNow=True)
        .start()
    )
    q.awaitTermination()
    return [e for e in emitted if e["user"] != "__flush__"]


def normalise(rows: list[dict]) -> list[tuple]:
    def utc(d: datetime) -> datetime:
        return d if d.tzinfo else d.replace(tzinfo=UTC)

    return sorted((r["user"], utc(r["start"]), utc(r["end"]), r["count"]) for r in rows)
