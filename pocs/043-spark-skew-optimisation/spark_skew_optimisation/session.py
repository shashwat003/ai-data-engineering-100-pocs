"""Local Spark session with settings recorded so benchmarks state their hardware and config."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess

from pyspark.sql import SparkSession

SHUFFLE_PARTITIONS = 32


def get_spark(cores: int = 4, memory: str = "4g", shuffle_partitions: int = SHUFFLE_PARTITIONS):
    return (
        SparkSession.builder.master(f"local[{cores}]")
        .appName("spark-skew-optimisation")
        .config("spark.driver.memory", memory)
        .config("spark.sql.shuffle.partitions", shuffle_partitions)
        .config(
            "spark.sql.files.maxPartitionBytes", 4 * 1024 * 1024
        )  # small data: keep input splits parallel
        .config("spark.ui.enabled", "true")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.ansi.enabled", "false")
        .getOrCreate()
    )


def environment(spark) -> dict:
    java = "unknown"
    if shutil.which("java"):
        out = subprocess.run(["java", "-version"], capture_output=True, text=True).stderr
        java = next((ln for ln in out.splitlines() if "version" in ln), "unknown").strip()
    mem_gb = None
    try:
        mem_gb = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except (ValueError, OSError):
        pass
    return {
        "spark": spark.version,
        "master": spark.sparkContext.master,
        "java": java,
        "python": platform.python_version(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
        "physical_memory_gb": mem_gb,
        "driver_memory": spark.conf.get("spark.driver.memory"),
        "shuffle_partitions": spark.conf.get("spark.sql.shuffle.partitions"),
    }
