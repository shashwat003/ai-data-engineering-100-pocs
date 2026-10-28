"""Apache Iceberg table operations with PyIceberg on a local SQLite catalog and local files.

Everything here is real Iceberg (metadata, manifests, snapshots, partition specs); only the engine is PyIceberg's
pure-Python scan/write path instead of Athena/Spark.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import DayTransform
from pyiceberg.types import DoubleType, IntegerType, LongType, NestedField, TimestampType

ORDERS = Schema(
    NestedField(1, "order_id", LongType(), required=True),
    NestedField(2, "customer_id", LongType(), required=True),
    NestedField(3, "ts", TimestampType(), required=True),
    NestedField(4, "amount", DoubleType(), required=False),
    NestedField(5, "quantity", IntegerType(), required=False),
)
ARROW = pa.schema(
    [
        pa.field("order_id", pa.int64(), nullable=False),
        pa.field("customer_id", pa.int64(), nullable=False),
        pa.field("ts", pa.timestamp("us"), nullable=False),
        pa.field("amount", pa.float64()),
        pa.field("quantity", pa.int32()),
    ]
)
DAY_SPEC = PartitionSpec(
    PartitionField(source_id=3, field_id=1000, transform=DayTransform(), name="ts_day")
)
START = dt.datetime(2026, 3, 1)


def catalog(root: str | Path) -> SqlCatalog:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    cat = SqlCatalog(
        "local", uri=f"sqlite:///{root}/catalog.db", warehouse=f"file://{root}/warehouse"
    )
    if ("lake",) not in cat.list_namespaces():
        cat.create_namespace("lake")
    return cat


def make_orders(start_id: int, n: int, day: int, *, customers: int = 20) -> pa.Table:
    ts = [START + dt.timedelta(days=day, seconds=60 * i) for i in range(n)]
    return pa.table(
        {
            "order_id": list(range(start_id, start_id + n)),
            "customer_id": [(start_id + i) % customers for i in range(n)],
            "ts": ts,
            "amount": [round(5 + ((start_id + i) % 17) * 3.1, 2) for i in range(n)],
            "quantity": pa.array([1 + (i % 4) for i in range(n)], pa.int32()),
        },
        schema=ARROW,
    )


def data_files(table) -> list[str]:
    return sorted(t.file.file_path for t in table.scan().plan_files())


def files_on_disk(table) -> set[str]:
    base = Path(table.location().removeprefix("file://")) / "data"
    return {f"file://{p}" for p in base.rglob("*.parquet")} if base.exists() else set()


def referenced_files(table) -> set[str]:
    """Data files referenced by ANY snapshot (needed for time travel)."""
    out: set[str] = set()
    for s in table.snapshots():
        out |= {t.file.file_path for t in table.scan(snapshot_id=s.snapshot_id).plan_files()}
    return out


def compact(table) -> None:
    """Rewrite the whole table as fewer, larger files (copy-on-write overwrite); history stays queryable."""
    from pyiceberg.expressions import AlwaysTrue

    data = table.scan().to_arrow()
    table.overwrite(
        data.cast(ARROW) if data.schema != ARROW else data, overwrite_filter=AlwaysTrue()
    )


def checksum(tbl: pa.Table) -> tuple[int, float]:
    return tbl.num_rows, round(sum(x for x in tbl.column("order_id").to_pylist()) + 0.0, 2)
