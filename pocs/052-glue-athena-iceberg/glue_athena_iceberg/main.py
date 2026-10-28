"""POC 052: Iceberg schema/partition evolution, time travel, compaction, deletes and maintenance; Glue/Athena config on a mock."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
from pyiceberg.exceptions import CommitFailedException
from pyiceberg.expressions import EqualTo
from pyiceberg.transforms import MonthTransform
from pyiceberg.types import LongType, StringType

from pocs_common import write_json

from .lakehouse import (
    ARROW,
    DAY_SPEC,
    ORDERS,
    START,
    catalog,
    checksum,
    compact,
    data_files,
    files_on_disk,
    make_orders,
    referenced_files,
)

POC_DIR = Path(__file__).resolve().parent.parent


def run(with_terraform: bool = True) -> dict:
    import datetime as dt

    res: dict = {}
    with tempfile.TemporaryDirectory() as t:
        cat = catalog(t)
        tbl = cat.create_table("lake.orders", ORDERS, partition_spec=DAY_SPEC)
        # 1) many small appends over 10 days -> many small files
        oid = 1
        for i in range(30):
            tbl.append(make_orders(oid, 40, day=i % 10))
            oid += 40
        res["small_appends"] = {
            "snapshots": len(tbl.snapshots()),
            "data_files": len(data_files(tbl)),
            "rows": tbl.scan().to_arrow().num_rows,
        }
        scan = tbl.scan(
            row_filter=f"ts >= '{(START + dt.timedelta(days=3)).isoformat()}' AND ts < '{(START + dt.timedelta(days=4)).isoformat()}'"
        )
        res["partition_pruning"] = {
            "files_planned_for_one_day": len(list(scan.plan_files())),
            "files_total": len(data_files(tbl)),
            "rows_returned": scan.to_arrow().num_rows,
        }
        # 2) compaction keeps results identical, reduces files, old snapshot still readable
        before = checksum(tbl.scan().to_arrow())
        snap_before = tbl.current_snapshot().snapshot_id
        compact(tbl)
        res["compaction"] = {
            "data_files_after": len(data_files(tbl)),
            "same_checksum": checksum(tbl.scan().to_arrow()) == before,
            "old_snapshot_rows": tbl.scan(snapshot_id=snap_before).to_arrow().num_rows,
        }
        # 3) schema evolution: add, rename, widen; old files stay readable (field ids, not names)
        with tbl.update_schema() as u:
            u.add_column("currency", StringType())
            u.rename_column("amount", "total_amount")
            u.update_column("quantity", LongType())
        tbl.append(
            make_orders(oid, 5, day=11)
            .rename_columns(["order_id", "customer_id", "ts", "total_amount", "quantity"])
            .append_column("currency", pa.array(["EUR"] * 5))
            .cast(
                pa.schema(
                    [
                        pa.field("order_id", pa.int64(), nullable=False),
                        pa.field("customer_id", pa.int64(), nullable=False),
                        pa.field("ts", pa.timestamp("us"), nullable=False),
                        pa.field("total_amount", pa.float64()),
                        pa.field("quantity", pa.int64()),
                        pa.field("currency", pa.string()),
                    ]
                )
            )
        )
        cur = tbl.scan().to_arrow()
        res["schema_evolution"] = {
            "columns": cur.column_names,
            "old_rows_currency_null": cur.filter(pc.field("currency").is_null()).num_rows,
            "new_rows_currency_eur": cur.filter(pc.field("currency") == "EUR").num_rows,
            "old_rows_total_amount_non_null": cur.column("total_amount").null_count == 0,
        }
        # 4) partition evolution: new data partitioned by month, old data still by day
        with tbl.update_spec() as s:
            s.remove_field("ts_day")
            s.add_field("ts", MonthTransform(), "ts_month")
        res["partition_evolution"] = {
            "specs": len(tbl.specs()),
            "default_spec_fields": [f.name for f in tbl.spec().fields],
        }
        # 5) delete one customer (right-to-erasure style), time travel shows the earlier state
        snap_pre_delete = tbl.current_snapshot().snapshot_id
        n_pre = tbl.scan(row_filter=EqualTo("customer_id", 7)).to_arrow().num_rows
        tbl.delete(delete_filter=EqualTo("customer_id", 7))
        res["delete"] = {
            "rows_for_customer_before": n_pre,
            "rows_after": tbl.scan(row_filter=EqualTo("customer_id", 7)).to_arrow().num_rows,
            "still_visible_in_old_snapshot": tbl.scan(
                snapshot_id=snap_pre_delete, row_filter=EqualTo("customer_id", 7)
            )
            .to_arrow()
            .num_rows,
        }
        files_before_expire = files_on_disk(tbl)
        tbl.maintenance.expire_snapshots().older_than(
            dt.datetime.now(dt.UTC) + dt.timedelta(seconds=1)
        ).commit()
        tbl.refresh()
        orphans = files_on_disk(tbl) - referenced_files(tbl)
        res["expire_snapshots"] = {
            "snapshots_left": len(tbl.snapshots()),
            "files_on_disk_before": len(files_before_expire),
            "unreferenced_files_on_disk_after": len(orphans),
        }
        for f in orphans:
            Path(f.removeprefix("file://")).unlink()
        res["expire_snapshots"]["deleted_data_removed_physically"] = len(files_on_disk(tbl)) == len(
            referenced_files(tbl)
        )
        # 6) enforcement
        errors = {}
        for label, bad in (
            (
                "nullable column given nulls-allowed schema",
                make_orders(900, 2, 0).cast(
                    ARROW.remove(0).insert(0, pa.field("order_id", pa.int64(), nullable=True))
                ),
            ),
            ("missing required column", make_orders(900, 2, 0).drop_columns(["customer_id"])),
        ):
            try:
                tbl.append(bad)
                errors[label] = "ACCEPTED"
            except Exception as e:
                errors[label] = type(e).__name__
        res["schema_enforcement"] = errors
        # 7) two writers, both commit (the second refreshes and retries)
        a, b = cat.load_table("lake.orders"), cat.load_table("lake.orders")
        cols = [f.name for f in a.schema().fields]

        def mk(start):
            return (
                make_orders(start, 3, 12)
                .rename_columns(["order_id", "customer_id", "ts", "total_amount", "quantity"])
                .append_column("currency", pa.array(["USD"] * 3))
                .select(cols)
            )

        try:
            a.append(mk(5000).cast(a.schema().as_arrow()))
            b.append(mk(6000).cast(b.schema().as_arrow()))
            res["concurrent_writers"] = "both commits succeeded"
        except CommitFailedException:
            res["concurrent_writers"] = "second writer failed after retries"
        res["final_rows"] = cat.load_table("lake.orders").scan().to_arrow().num_rows
    res["glue_athena_on_mock"] = _mock_stack() if with_terraform else {"status": "SKIPPED"}
    return res


def _mock_stack() -> dict:
    from aws_mock_harness.server import MockAws

    from .mock_stack import apply_and_inspect, terraform_available

    if not terraform_available():
        return {"status": "NOT RUN", "reason": "terraform binary not found"}
    with MockAws() as aws:
        return {"status": "RAN against local moto, not real AWS", **apply_and_inspect(aws)}


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    md = [
        "# Results (measured by `python -m glue_athena_iceberg.main`; real PyIceberg 0.12 on a local SQLite catalog; no Athena/Spark engine)",
        "",
    ]
    for k, v in r.items():
        md.append(f"- **{k}**: {v}")
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
