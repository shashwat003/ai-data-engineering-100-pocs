"""Small-file problem lab: generate streaming-style Parquet files, compact them safely, measure effects."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import statistics
import time
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

SCHEMA = pa.schema(
    [
        ("event_id", pa.int64()),
        ("user_id", pa.int32()),
        ("day", pa.int32()),
        ("amount", pa.float64()),
        ("country", pa.string()),
    ]
)
COUNTRIES = np.array(["US", "DE", "IN", "BR", "JP", "GB", "CA", "AU"])


def batch(rng: np.random.Generator, start_id: int, n: int, n_days: int = 90) -> pa.Table:
    """One micro-batch of events; days are random so every file spans the whole date range (worst case)."""
    return pa.table({"event_id": np.arange(start_id, start_id + n, dtype=np.int64), "user_id": rng.integers(0, 50_000, n, dtype=np.int32),
                     "day": rng.integers(0, n_days, n, dtype=np.int32), "amount": np.round(rng.gamma(2.0, 20.0, n), 2),
                     "country": COUNTRIES[rng.integers(0, len(COUNTRIES), n)]}, schema=SCHEMA)  # fmt: skip


def write_small_files(
    out: Path, n_files: int, rows_per_file: int, seed: int = 1, compression: str = "snappy"
) -> None:
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    rng = np.random.default_rng(seed)
    for i in range(n_files):
        pq.write_table(
            batch(rng, i * rows_per_file, rows_per_file),
            out / f"part-{i:05d}.parquet",
            compression=compression,
        )


def table_digest(path: Path) -> tuple[int, str]:
    """Row count and order-independent checksum of all rows (sum of per-row hashes mod 2^64)."""
    con = duckdb.connect()
    n, h = con.execute(
        f"SELECT count(*), COALESCE(sum(hash(event_id, user_id, day, amount, country) % 1000000007), 0) FROM read_parquet('{path}/*.parquet')"
    ).fetchone()
    return int(n), str(h)


def compact(
    src: Path,
    dst: Path,
    *,
    row_group_rows: int = 250_000,
    sort_by: str | None = "day",
    compression: str = "zstd",
    target_rows_per_file: int = 1_000_000,
) -> dict:
    """Rewrite `src` into `dst` as few large files, optionally sorted so row-group min/max stats can prune.

    Writes to a temporary directory, verifies row count + checksum, then swaps with an atomic rename so
    readers see either all old files or all new files. The old directory is kept under a `.old` name
    until the caller removes it (rollback path).
    """
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    expected = table_digest(src)
    con = duckdb.connect()
    order = f"ORDER BY {sort_by}" if sort_by else ""
    total = expected[0]
    n_files = max(1, -(-total // target_rows_per_file))
    con.execute(f"CREATE TEMP TABLE t AS SELECT * FROM read_parquet('{src}/*.parquet') {order}")
    per = -(-total // n_files)
    for i in range(n_files):
        con.execute(
            f"COPY (SELECT * FROM t LIMIT {per} OFFSET {i * per}) TO '{tmp}/part-{i:05d}.parquet' (FORMAT parquet, COMPRESSION {compression}, ROW_GROUP_SIZE {row_group_rows})"
        )
    if table_digest(tmp) != expected:
        shutil.rmtree(tmp)
        raise RuntimeError(
            "compaction verification failed: row count/checksum differ; nothing was swapped"
        )
    old = dst.with_name(dst.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if dst.exists():
        os.rename(dst, old)
    os.rename(tmp, dst)
    (dst / "_manifest.json").write_text(
        json.dumps({"rows": total, "files": n_files, "sorted_by": sort_by, "digest": expected[1]})
    )
    return {"files": n_files, "rows": total}


def layout(path: Path) -> dict:
    files = sorted(path.glob("*.parquet"))
    sizes = [f.stat().st_size for f in files]
    rgs = [pq.ParquetFile(f).metadata.num_row_groups for f in files]
    return {
        "files": len(files),
        "total_mb": round(sum(sizes) / 2**20, 2),
        "median_file_kb": round(statistics.median(sizes) / 1024, 1),
        "row_groups": sum(rgs),
    }


def prunable_row_groups(path: Path, column: str, lo: int, hi: int) -> tuple[int, int]:
    """(row groups whose min/max statistics rule out `lo <= column <= hi`, total row groups)."""
    skipped = total = 0
    for f in sorted(path.glob("*.parquet")):
        md = pq.ParquetFile(f).metadata
        idx = md.schema.names.index(column)
        for g in range(md.num_row_groups):
            st = md.row_group(g).column(idx).statistics
            total += 1
            if st is not None and st.has_min_max and (st.max < lo or st.min > hi):
                skipped += 1
    return skipped, total


QUERY = "SELECT country, count(*), round(sum(amount), 2) FROM read_parquet('{p}/*.parquet') WHERE day BETWEEN 10 AND 14 GROUP BY country ORDER BY country"


def time_query(path: Path, repeats: int = 5) -> dict:
    con = duckdb.connect()
    con.execute("PRAGMA threads=4")
    con.execute(QUERY.format(p=path)).fetchall()  # warm-up (discarded)
    ts = []
    for _ in range(repeats):
        t = time.perf_counter()
        rows = con.execute(QUERY.format(p=path)).fetchall()
        ts.append(time.perf_counter() - t)
    return {
        "median_ms": round(statistics.median(ts) * 1000, 1),
        "min_ms": round(min(ts) * 1000, 1),
        "max_ms": round(max(ts) * 1000, 1),
        "result_digest": hashlib.sha256(repr(rows).encode()).hexdigest()[:12],
    }
