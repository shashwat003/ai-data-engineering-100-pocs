"""Timing and per-task statistics from the Spark UI REST API (local mode)."""

from __future__ import annotations

import json
import statistics
import time
import urllib.request
from urllib.parse import urlparse

_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({})
)  # never route localhost via a proxy


def _get(url: str):
    with _OPENER.open(url, timeout=10) as resp:
        return json.load(resp)


def task_stats(spark, group: str) -> dict | None:
    """Task count, duration and shuffle-read distribution of the busiest stage in a job group.

    "Busiest" = most shuffle records read, i.e. the join/aggregation stage. Returns None if the UI
    is unavailable (the benchmark then reports wall-clock only).
    """
    sc = spark.sparkContext
    try:
        port = urlparse(sc.uiWebUrl).port
        base = f"http://127.0.0.1:{port}/api/v1/applications/{sc.applicationId}"
        job_ids = sc.statusTracker().getJobIdsForGroup(group)
        stage_ids = {
            s
            for j in job_ids
            for s in (
                sc.statusTracker().getJobInfo(j).stageIds
                if sc.statusTracker().getJobInfo(j)
                else []
            )
        }
        stages = [
            s
            for s in _get(f"{base}/stages?status=complete")
            if s["stageId"] in stage_ids and s["numCompleteTasks"] > 0
        ]
        if not stages:
            return None
        busiest = max(stages, key=lambda s: s.get("shuffleReadRecords", 0))
        tasks = _get(
            f"{base}/stages/{busiest['stageId']}/{busiest['attemptId']}/taskList?length=100000"
        )
        dur = [t["duration"] for t in tasks if t.get("status") == "SUCCESS"]
        rec = [
            t["taskMetrics"]["shuffleReadMetrics"]["recordsRead"]
            for t in tasks
            if t.get("taskMetrics")
        ]
    except Exception:  # UI/REST availability varies; timing is still valid without it
        return None
    if not dur or not rec:
        return None
    return {
        "tasks": len(dur),
        "task_ms_max": max(dur),
        "task_ms_median": statistics.median(dur),
        "task_ms_sum": sum(dur),
        "records_max": max(rec),
        "records_median": statistics.median(rec),
        "records_max_over_median": round(max(rec) / statistics.median(rec), 2)
        if statistics.median(rec)
        else None,
    }


def time_runs(spark, label: str, action, *, repeats: int = 5, warmup: int = 1) -> dict:
    """Run `action()` warmup + repeats times; wall-clock per run, plus task stats of the last run."""
    for _ in range(warmup):
        action()
    secs = []
    for i in range(repeats):
        spark.catalog.clearCache()
        group = f"{label}-{i}"
        spark.sparkContext.setJobGroup(group, label)
        t = time.perf_counter()
        action()
        secs.append(time.perf_counter() - t)
    return {
        "label": label,
        "repeats": repeats,
        "seconds": [round(s, 2) for s in secs],
        "median_s": round(statistics.median(secs), 2),
        "min_s": round(min(secs), 2),
        "max_s": round(max(secs), 2),
        "stdev_s": round(statistics.stdev(secs), 2) if len(secs) > 1 else 0.0,
        "tasks": task_stats(spark, f"{label}-{repeats - 1}"),
    }
