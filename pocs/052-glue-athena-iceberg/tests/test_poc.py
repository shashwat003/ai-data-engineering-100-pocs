import datetime as dt

import pyarrow as pa
import pytest
from glue_athena_iceberg.lakehouse import (
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
from glue_athena_iceberg.main import run
from glue_athena_iceberg.mock_stack import terraform_available
from pyiceberg.expressions import EqualTo
from pyiceberg.transforms import MonthTransform
from pyiceberg.types import LongType, StringType


@pytest.fixture()
def tbl(tmp_path):
    t = catalog(tmp_path).create_table("lake.orders", ORDERS, partition_spec=DAY_SPEC)
    oid = 1
    for i in range(12):
        t.append(make_orders(oid, 20, day=i % 4))
        oid += 20
    return t


def day_filter(d):
    a, b = START + dt.timedelta(days=d), START + dt.timedelta(days=d + 1)
    return f"ts >= '{a.isoformat()}' AND ts < '{b.isoformat()}'"


def test_partition_pruning_plans_fewer_files(tbl):
    scan = tbl.scan(row_filter=day_filter(1))
    assert (
        0 < len(list(scan.plan_files())) < len(data_files(tbl)) and scan.to_arrow().num_rows == 60
    )


def test_compaction_preserves_data_reduces_files_keeps_history(tbl):
    before, snap, files = (
        checksum(tbl.scan().to_arrow()),
        tbl.current_snapshot().snapshot_id,
        len(data_files(tbl)),
    )
    compact(tbl)
    assert checksum(tbl.scan().to_arrow()) == before and len(data_files(tbl)) < files
    assert tbl.scan(snapshot_id=snap).to_arrow().num_rows == 240


def test_schema_evolution_old_files_remain_readable(tbl):
    with tbl.update_schema() as u:
        u.add_column("currency", StringType())
        u.rename_column("amount", "total_amount")
        u.update_column("quantity", LongType())
    t = tbl.scan().to_arrow()
    assert (
        t.column_names[-1] == "currency"
        and "total_amount" in t.column_names
        and t.column("currency").null_count == t.num_rows
    )
    assert t.column("total_amount").null_count == 0  # data written as 'amount' is found by field id
    assert str(t.schema.field("quantity").type) == "int64"


def test_incompatible_writes_are_rejected(tbl):
    with pytest.raises(ValueError):
        tbl.append(make_orders(900, 2, 0).drop_columns(["customer_id"]))
    loose = ARROW.set(0, pa.field("order_id", pa.int64(), nullable=True))
    with pytest.raises(ValueError):
        tbl.append(make_orders(900, 2, 0).cast(loose))
    wrong = make_orders(900, 2, 0).set_column(3, "amount", pa.array(["x", "y"]))
    with pytest.raises((ValueError, pa.ArrowInvalid, TypeError)):
        tbl.append(wrong)


def test_partition_evolution_mixed_specs_still_scan_correctly(tbl):
    n = tbl.scan().to_arrow().num_rows
    with tbl.update_spec() as s:
        s.remove_field("ts_day")
        s.add_field("ts", MonthTransform(), "ts_month")
    tbl.append(make_orders(1000, 30, day=2))
    assert len(tbl.specs()) == 2 and tbl.scan().to_arrow().num_rows == n + 30
    assert tbl.scan(row_filter=day_filter(2)).to_arrow().num_rows == 60 + 30


def test_delete_time_travel_and_physical_removal_after_expiry(tbl):
    pre = tbl.current_snapshot().snapshot_id
    rows = tbl.scan(row_filter=EqualTo("customer_id", 7)).to_arrow().num_rows
    assert rows > 0
    tbl.delete(delete_filter=EqualTo("customer_id", 7))
    assert tbl.scan(row_filter=EqualTo("customer_id", 7)).to_arrow().num_rows == 0
    assert (
        tbl.scan(snapshot_id=pre, row_filter=EqualTo("customer_id", 7)).to_arrow().num_rows == rows
    )  # history still holds it
    tbl.maintenance.expire_snapshots().older_than(
        dt.datetime.now(dt.UTC) + dt.timedelta(seconds=1)
    ).commit()
    tbl.refresh()
    assert len(tbl.snapshots()) == 1
    assert files_on_disk(tbl) - referenced_files(
        tbl
    )  # expiry alone does not delete files: orphan cleanup is a separate step


def test_upsert_updates_and_inserts(tbl):
    base = tbl.scan().to_arrow().num_rows
    upd = make_orders(1, 2, day=0).set_column(3, "amount", pa.array([999.0, 998.0]))
    new = make_orders(7000, 1, day=0)
    tbl.upsert(pa.concat_tables([upd, new]), join_cols=["order_id"])
    now = tbl.scan(row_filter="order_id = 1").to_arrow()
    assert tbl.scan().to_arrow().num_rows == base + 1 and now.column("amount").to_pylist() == [
        999.0
    ]


def test_two_writers_both_commit(tmp_path):
    cat = catalog(tmp_path)
    cat.create_table("lake.t", ORDERS, partition_spec=DAY_SPEC)
    a, b = cat.load_table("lake.t"), cat.load_table("lake.t")
    a.append(make_orders(1, 5, 0))
    b.append(make_orders(100, 5, 1))  # b's metadata is stale; PyIceberg refreshes and retries
    assert cat.load_table("lake.t").scan().to_arrow().num_rows == 10


def test_namespace_creation_is_idempotent(tmp_path):
    catalog(tmp_path)
    assert ("lake",) in catalog(tmp_path).list_namespaces()


@pytest.mark.skipif(not terraform_available(), reason="terraform required (heavy CI job)")
def test_demo_end_to_end_including_mock_stack():
    r = run()
    assert (
        r["partition_pruning"]["files_planned_for_one_day"] < r["partition_pruning"]["files_total"]
    )
    assert (
        r["compaction"]["same_checksum"]
        and r["compaction"]["data_files_after"] < r["small_appends"]["data_files"]
    )
    assert (
        r["schema_evolution"]["old_rows_currency_null"] > 0
        and r["partition_evolution"]["specs"] == 2
    )
    assert r["delete"]["rows_after"] == 0 and r["delete"]["still_visible_in_old_snapshot"] > 0
    assert (
        r["expire_snapshots"]["deleted_data_removed_physically"]
        and r["concurrent_writers"] == "both commits succeeded"
    )
    g = r["glue_athena_on_mock"]
    assert (
        g["replan_exit_code"] == 0
        and g["planned_workgroup"]["enforced"] is True
        and g["lake_public_access_blocked"]
        and g["results_bucket_encrypted"]
    )
