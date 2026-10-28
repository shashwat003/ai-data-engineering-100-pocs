import json
import shutil
from datetime import date

import pytest
from data_contracts_ci_gate.contract import load_contract
from data_contracts_ci_gate.validate import validate_dataset
from medallion_lakehouse_duckdb.generate import INJECTED, generate_landing
from medallion_lakehouse_duckdb.lakehouse import (
    CONTRACT,
    BatchConflict,
    BatchOrderError,
    GateFailure,
    Lakehouse,
    SchemaError,
)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("lh")
    manifest = generate_landing(root / "landing")
    lake = Lakehouse(root / "lake")
    report = lake.run(root / "landing")
    yield lake, manifest, report, root
    lake.close()


@pytest.fixture
def fresh(tmp_path):
    manifest = generate_landing(tmp_path / "landing")
    lake = Lakehouse(tmp_path / "lake")
    yield lake, manifest, tmp_path
    lake.close()


def test_all_gates_pass_on_clean_run(built):
    _, _, report, _ = built
    assert len(report.checks) == 8
    assert all(c.passed for c in report.checks), [c for c in report.checks if not c.passed]


def test_quarantine_matches_injected_defects_exactly(built):
    lake, manifest, _, _ = built
    got = {
        (b, r): n
        for b, r, n in lake.con.execute(
            "SELECT batch_id, reason, count(*) FROM silver_quarantine GROUP BY ALL"
        ).fetchall()
    }
    expected = {(f"b{k:03d}", r): n for k, d in INJECTED.items() for r, n in d.items()}
    assert got == expected


def test_rows_reconcile_per_batch(built):
    lake, _, _, _ = built
    rows = lake.con.execute("SELECT bronze_rows, accepted, quarantined FROM ledger").fetchall()
    assert rows and all(b == a + q for b, a, q in rows)


def test_silver_has_no_bad_rows_and_quarantine_has_no_pii(built):
    lake, _, _, _ = built
    assert (
        lake.con.execute("SELECT count(*) FROM silver_orders WHERE amount < 0").fetchone()[0] == 0
    )
    assert (
        lake.con.execute("SELECT count(*) FROM silver_quarantine WHERE raw LIKE '%@%'").fetchone()[
            0
        ]
        == 0
    )


def test_late_updates_applied_latest_batch_wins(built):
    lake, manifest, _, root = built
    oid = int(manifest["resent"]["b003"][0])
    status, batch = lake.con.execute(
        "SELECT status, _batch_id FROM silver_orders WHERE order_id=?", [oid]
    ).fetchone()
    resent = [
        line for line in (root / "landing/b003/orders.csv").read_text().splitlines()
        if line.startswith(f"{oid},")
    ]  # fmt: skip
    assert resent and resent[0].split(",")[4] == status and batch >= "b003"


def test_replay_is_a_noop(fresh):
    lake, _, tmp = fresh
    lake.run(tmp / "landing")
    h, ledger = (
        lake.content_hash(),
        lake.con.execute("SELECT * FROM ledger ORDER BY 1,2").fetchall(),
    )
    again = lake.run(tmp / "landing")
    assert not again.ingested and len(again.skipped) == 8
    assert lake.content_hash() == h
    assert lake.con.execute("SELECT * FROM ledger ORDER BY 1,2").fetchall() == ledger


def test_incremental_equals_full_rebuild_from_bronze(fresh):
    lake, _, tmp = fresh
    landing = tmp / "landing"
    staged = tmp / "staged"
    for b in sorted(p.name for p in landing.iterdir()):  # arrive one batch at a time
        shutil.copytree(landing / b, staged / b)
        lake.run(staged)
    incremental = lake.content_hash()
    lake.rebuild_from_bronze()
    assert lake.content_hash() == incremental


def test_same_batch_different_bytes_is_rejected_and_changes_nothing(fresh):
    lake, _, tmp = fresh
    lake.run(tmp / "landing")
    h = lake.content_hash()
    with (tmp / "landing/b001/orders.csv").open("a") as fh:
        fh.write("999999,1,2025-01-01,,completed,5.00,x@example.com\n")
    with pytest.raises(BatchConflict):
        lake.run(tmp / "landing")
    assert lake.content_hash() == h


def test_out_of_order_order_replay_does_not_regress(fresh):
    lake, _, tmp = fresh
    lake.run(tmp / "landing")
    oid, status = lake.con.execute(
        "SELECT order_id, status FROM silver_orders WHERE _batch_id='b004' LIMIT 1"
    ).fetchone()
    old = tmp / "old"
    (old / "b000").mkdir(parents=True)
    other = "returned" if status != "returned" else "completed"
    (old / "b000/orders.csv").write_text(
        "order_id,customer_id,order_date,ship_date,status,amount,email\n"
        f"{oid},1,2025-01-01,,{other},9.99,a@example.com\n"
    )
    (old / "b000/customers.csv").write_text("customer_id,segment,country\n")
    (old / "b000/_meta.json").write_text(
        json.dumps({"batch_id": "b000", "effective_date": "2020-01-01"})
    )
    with pytest.raises(BatchOrderError):  # empty customer snapshot is older than applied history
        lake.run(old)
    lake.ingest_bronze("orders", "b000", old / "b000/orders.csv", date(2020, 1, 1))
    lake.promote_orders("b000")
    assert (
        lake.con.execute("SELECT status FROM silver_orders WHERE order_id=?", [oid]).fetchone()[0]
        == status
    )


def test_scd2_history_is_contiguous_and_point_in_time_correct(built):
    lake, manifest, _, _ = built
    cid = int(manifest["customer_changes"]["b002"][0])
    versions = lake.con.execute(
        "SELECT segment, valid_from, valid_to, is_current FROM dim_customer "
        "WHERE customer_id=? ORDER BY valid_from",
        [cid],
    ).fetchall()
    assert len(versions) >= 2 and sum(v[3] for v in versions) == 1
    assert versions[-1][2] == date(9999, 12, 31)
    assert all(a[2] == b[1] for a, b in zip(versions, versions[1:], strict=False))
    # gold shows the segment valid on each order date, not today's segment
    bad = lake.con.execute(
        """SELECT count(*) FROM gold_fct_orders f JOIN silver_orders o USING (order_id)
           JOIN dim_customer d ON d.customer_sk = f.customer_sk
           WHERE NOT (o.order_date >= d.valid_from AND o.order_date < d.valid_to)"""
    ).fetchone()[0]
    assert bad == 0
    pairs = lake.con.execute(
        """SELECT count(DISTINCT f.segment_at_order) FROM gold_fct_orders f
           JOIN silver_orders o USING (order_id) WHERE o.customer_id=?""",
        [cid],
    ).fetchone()[0]
    assert pairs >= 1


def test_scd2_change_count_equals_generated_changes(built):
    lake, manifest, _, _ = built
    changes = sum(len(v) for v in manifest["customer_changes"].values())
    extra = lake.con.execute(
        "SELECT count(*) - count(DISTINCT customer_id) FROM dim_customer"
    ).fetchone()[0]
    assert extra == changes


def test_scd2_out_of_order_snapshot_is_rejected(fresh):
    lake, _, tmp = fresh
    lake.run(tmp / "landing")
    bad = tmp / "late"
    shutil.copytree(tmp / "landing/b002", bad / "a000")
    (bad / "a000/_meta.json").write_text(
        json.dumps({"batch_id": "a000", "effective_date": "2000-01-01"})
    )
    lake.ingest_bronze("customers", "a000", bad / "a000/customers.csv", date(2000, 1, 1))
    with pytest.raises(BatchOrderError):
        lake.promote_customers("a000")


def test_contract_gate_blocks_publication_and_rolls_back(fresh):
    lake, _, tmp = fresh
    lake.run(tmp / "landing")
    h = lake.content_hash()
    oid, status = lake.con.execute("SELECT order_id, status FROM silver_orders LIMIT 1").fetchone()
    lake.con.execute("UPDATE silver_orders SET status='bogus' WHERE order_id=?", [oid])
    with pytest.raises(GateFailure, match="data contract"):
        lake.build_gold()
    lake.con.execute("UPDATE silver_orders SET status=? WHERE order_id=?", [status, oid])
    lake.build_gold()  # recovers once silver is valid again, with identical content
    assert lake.content_hash() == h


def test_gold_not_exported_when_gate_fails(fresh):
    lake, _, tmp = fresh
    lake.run(tmp / "landing")
    stamp = (tmp / "lake/gold/gold_fct_orders.parquet").stat().st_mtime_ns
    lake.con.execute(
        "UPDATE silver_orders SET amount = -1 WHERE order_id=(SELECT min(order_id) FROM silver_orders)"
    )
    with pytest.raises(GateFailure):
        lake.build_gold()
    assert (tmp / "lake/gold/gold_fct_orders.parquet").stat().st_mtime_ns == stamp


def test_orphan_orders_fail_the_gate(fresh):
    lake, _, tmp = fresh
    lake.run(tmp / "landing")
    lake.con.execute(
        "DELETE FROM dim_customer WHERE customer_id=(SELECT min(customer_id) FROM dim_customer)"
    )
    with pytest.raises(GateFailure, match="without a customer version"):
        lake.build_gold()


def test_bronze_matches_contract_report_from_poc077(built):
    """The standalone POC 077 validator and silver quarantine agree on batch b002."""
    _, _, _, root = built
    contract = load_contract(CONTRACT)
    report = validate_dataset(contract, root / "landing/b002/orders.csv", as_of=date(2030, 1, 1))
    c = report.counts()
    assert c["column.required:customer_id"] == INJECTED[2]["required:customer_id"]
    assert c["column.min:amount"] == INJECTED[2]["range:amount_min"]
    assert c["column.max:amount"] == INJECTED[2]["range:amount_max"]
    assert c["column.enum:status"] == INJECTED[2]["enum:status"]
    assert c["column.pattern:email"] == INJECTED[2]["pattern:email"]
    assert c["column.unique:order_id"] == INJECTED[2]["unique:order_id"]


def test_bad_batch_id_and_missing_columns_rejected(fresh):
    lake, _, tmp = fresh
    src = tmp / "landing/b001/orders.csv"
    with pytest.raises(SchemaError):
        lake.ingest_bronze("orders", "../../evil", src, date(2025, 1, 1))
    broken = tmp / "broken.csv"
    broken.write_text("order_id,amount\n1,2\n")
    with pytest.raises(SchemaError, match="missing columns"):
        lake.ingest_bronze("orders", "bx", broken, date(2025, 1, 1))
    assert lake.con.execute("SELECT count(*) FROM ledger").fetchone()[0] == 0


def test_hostile_text_is_data_not_sql(fresh):
    lake, _, tmp = fresh
    f = tmp / "hostile.csv"
    f.write_text(
        "order_id,customer_id,order_date,ship_date,status,amount,email\n"
        '1,1,2025-01-01,,"x\'); DROP TABLE silver_orders;--",5.00,a@example.com\n'
    )
    lake.ingest_bronze("orders", "bh", f, date(2025, 1, 1))
    assert lake.promote_orders("bh") == (0, 1)
    assert lake.con.execute("SELECT count(*) FROM silver_orders").fetchone()[0] == 0  # table intact
