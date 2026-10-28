import json
import shutil
from pathlib import Path

import duckdb
import pytest
from redshift_serverless_elt import data, elt, pipeline, redshift_sql
from redshift_serverless_elt.costs import check_plan, monthly_cost, run_cost
from redshift_serverless_elt.loader import CopyError, Warehouse, copy_sql

ROLE = "arn:aws:iam::123456789012:role/redshift-copy-role"


@pytest.fixture(scope="module")
def landing(tmp_path_factory):
    root = tmp_path_factory.mktemp("elt")
    manifest = data.generate(root / "landing")
    return root, manifest


# ---------- COPY semantics ----------


def test_copy_rejects_unparsable_cells_and_logs_them(landing, tmp_path):
    root, manifest = landing
    w = Warehouse(tmp_path / "w.duckdb")
    loads = w.load_landing(root / "landing")
    assert sum(x["rows_rejected"] for x in loads) == manifest["expected"]["copy_rejects"]
    errs = w.con.execute("SELECT column_name, raw_value FROM raw.load_errors").fetchall()
    assert errs == [("amount", "12,50")] * 3
    total_rows = sum(b["orders"] for b in manifest["batches"])
    assert w.con.execute("SELECT COUNT(*) FROM raw.orders").fetchone()[0] == total_rows - 3
    w.close()


def test_copy_is_atomic_past_maxerror(landing, tmp_path):
    root, _ = landing
    w = Warehouse(tmp_path / "w.duckdb")
    with pytest.raises(CopyError, match="nothing loaded"):
        w.copy("orders", root / "landing/b002/orders.csv", "b002", maxerror=1)
    assert w.con.execute("SELECT COUNT(*) FROM raw.orders").fetchone()[0] == 0
    assert w.con.execute("SELECT COUNT(*) FROM raw.copy_ledger").fetchone()[0] == 0
    assert (
        w.copy("orders", root / "landing/b002/orders.csv", "b002", maxerror=3)["rows_rejected"] == 3
    )
    w.close()


def test_replaying_a_file_is_skipped_and_changed_content_is_refused(landing, tmp_path):
    root, _ = landing
    w = Warehouse(tmp_path / "w.duckdb")
    src = root / "landing/b001/customers.csv"
    assert w.copy("customers", src, "b001")["status"] == "loaded"
    assert w.copy("customers", src, "b001")["status"] == "skipped"
    changed = tmp_path / "c.csv"
    changed.write_text(src.read_text().replace("consumer", "smb", 1))
    with pytest.raises(CopyError, match="different content"):
        w.copy("customers", changed, "b001")
    w.close()


def test_load_timestamps_strictly_increase(landing, tmp_path):
    root, _ = landing
    w = Warehouse(tmp_path / "w.duckdb")
    w.load_landing(root / "landing")
    ts = [
        r[0]
        for r in w.con.execute("SELECT DISTINCT _loaded_at FROM raw.orders ORDER BY 1").fetchall()
    ]
    assert len(ts) == 4 and ts == sorted(ts)
    w.close()


# ---------- COPY statement generation ----------


def test_copy_sql_is_valid_redshift_and_carries_the_options():
    sql = copy_sql("orders", "s3://bucket-name/b001/orders.csv", ROLE, maxerror=5)
    assert "MAXERROR 5" in sql and "IGNOREHEADER 1" in sql and "FORMAT AS CSV" in sql
    assert redshift_sql.lint_sql(sql) == []


@pytest.mark.parametrize("kw", [
    {"table": "orders; DROP TABLE x"}, {"table": "Orders"}, {"s3_uri": "https://bucket/x.csv"}, {"s3_uri": "s3://b/x'; --"},
    {"iam_role_arn": "arn:aws:iam::1:role/x"}, {"maxerror": -1}, {"maxerror": 10**6}, {"schema": "raw;"},
])  # fmt: skip
def test_copy_sql_rejects_injection_and_bad_options(kw):
    args = {"table": "orders", "s3_uri": "s3://bucket-name/o.csv", "iam_role_arn": ROLE, **kw}
    with pytest.raises(ValueError):
        copy_sql(**args)


# ---------- Redshift dialect checks ----------


def test_lint_flags_constructs_redshift_lacks():
    assert any("TRY_CAST" in f for f in redshift_sql.lint_sql("select try_cast(a as int) from t"))
    assert (
        redshift_sql.lint_sql("select a, row_number() over (partition by b order by c) from t")
        == []
    )
    assert redshift_sql.lint_sql("select a from")  # does not parse


def test_config_check_catches_bad_keys_and_strategies(tmp_path):
    import json

    nodes = {
        "m1": {"resource_type": "model", "name": "m1", "config": {"dist": "nope", "sort": ["a", "zzz"], "materialized": "incremental", "incremental_strategy": "insert_overwrite"}},
        "m2": {"resource_type": "model", "name": "m2", "config": {"dist": "a", "sort": "a", "materialized": "incremental", "incremental_strategy": "delete+insert"}},
        "m3": {"resource_type": "model", "name": "m3", "config": {"dist": "all"}},
    }  # fmt: skip
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"nodes": nodes}))
    out = redshift_sql.check_configs(p, {"m1": ["a"], "m2": ["a"], "m3": ["a"]})
    assert any("dist key 'nope'" in x for x in out) and any("sort key 'zzz'" in x for x in out)
    assert any("insert_overwrite" in x for x in out) and any(
        "m2: delete+insert needs a unique_key" in x for x in out
    )
    assert not any(x.startswith("m3") for x in out)


# ---------- real dbt build over the loaded warehouse ----------


@pytest.fixture(scope="module")
def built(landing, tmp_path_factory):
    root, manifest = landing
    work = tmp_path_factory.mktemp("built")
    db = work / "w.duckdb"
    w = Warehouse(db)
    w.load_landing(root / "landing")
    w.close()
    summary = elt.build(work, db)
    return work, db, summary, manifest


def q(db, sql, *args):
    con = duckdb.connect(str(db))
    rows = con.execute(sql, list(args)).fetchall()
    con.close()
    return rows


def test_dbt_build_passes_all_models_and_tests(built):
    _, _, summary, _ = built
    st = summary["by_status"]
    assert (
        summary["success"]
        and st.get("success") == 10
        and st.get("pass", 0) >= 17
        and not (st.keys() & {"fail", "error", "skipped"})
    )


def test_marts_reconcile_with_an_independent_asof_recomputation(built):
    _, db, _, manifest = built
    r = elt.reconcile(db)
    assert r["ok"] and r["rejected_orders"] == manifest["expected"]["sql_rejects"]
    assert r["orders_with_wrong_point_in_time_segment"] == 0 and r["segments_differ_between_rules"]


def test_rejects_are_quarantined_not_dropped_silently(built):
    _, db, _, _ = built
    assert dict(q(db, "SELECT reason, COUNT(*) FROM rejected_orders GROUP BY 1")) == {
        "status": 3,
        "amount": 2,
    }


def test_late_updates_take_the_highest_batch_version(built):
    _, db, _, _ = built
    n = q(
        db,
        "SELECT COUNT(*) FROM (SELECT order_id FROM raw.orders GROUP BY order_id HAVING COUNT(*) > 1)",
    )[0][0]
    wrong = q(
        db,
        """SELECT COUNT(*) FROM fct_orders f JOIN (SELECT order_id, arg_max(status, (_batch_id, _loaded_at)) s FROM raw.orders GROUP BY 1) r USING (order_id)
                     WHERE f.status <> r.s""",
    )[0][0]
    assert n >= 20 and wrong == 0


def test_compiled_models_parse_as_redshift_and_configs_are_valid(built):
    work, db, _, _ = built
    findings = redshift_sql.lint_compiled(work / "target")
    assert len(findings) == 10 and all(v == [] for v in findings.values())
    assert redshift_sql.check_configs(work / "target" / "manifest.json", pipeline.columns(db)) == []


# ---------- business rule: segment AS OF THE ORDER DATE (point-in-time), regression tests ----------


def test_history_has_one_row_per_customer_plus_one_per_real_change(built):
    _, db, _, _ = built
    snaps = q(
        db,
        "SELECT customer_id, _effective_date, segment, country FROM raw.customers ORDER BY customer_id, _effective_date",
    )
    expected, prev = 0, {}
    for cid, _eff, seg, ctry in snaps:
        if prev.get(cid) != (seg, ctry):
            expected += 1  # identical consecutive snapshots collapse
        prev[cid] = (seg, ctry)
    assert q(db, "SELECT COUNT(*) FROM dim_customer_history")[0][0] == expected
    assert (
        expected > q(db, "SELECT COUNT(DISTINCT customer_id) FROM dim_customer_history")[0][0]
    )  # some customers changed


def test_changed_segment_applies_from_its_effective_date_not_retroactively(built):
    _, db, _, _ = built
    cid, old, new, change = q(
        db,
        """SELECT customer_id, lag(segment) OVER w, segment, valid_from FROM dim_customer_history
                                    WINDOW w AS (PARTITION BY customer_id ORDER BY valid_from) QUALIFY lag(segment) OVER w IS NOT NULL LIMIT 1""",
    )[0]
    before = q(
        db,
        "SELECT DISTINCT segment FROM fct_orders WHERE customer_id=? AND order_date < ?",
        cid,
        change,
    )
    after = q(
        db,
        "SELECT DISTINCT segment FROM fct_orders WHERE customer_id=? AND order_date >= ?",
        cid,
        change,
    )
    assert old != new
    assert {r[0] for r in before} <= {old} and {r[0] for r in after} <= {new}
    assert (
        q(db, "SELECT segment FROM dim_customer WHERE customer_id=?", cid)[0][0] == new
    )  # the current dimension shows today's value


def test_orders_before_the_first_snapshot_use_the_earliest_known_state(built):
    _, db, _, _ = built
    assert (
        q(db, "SELECT COUNT(*) FROM dim_customer_history WHERE valid_from = DATE '1900-01-01'")[0][
            0
        ]
        == q(db, "SELECT COUNT(DISTINCT customer_id) FROM dim_customer_history")[0][0]
    )
    assert q(db, "SELECT COUNT(*) FROM fct_orders WHERE segment IS NULL")[0][0] == 0


def test_late_arriving_orders_get_the_segment_of_their_order_date(built):
    _, db, _, manifest = built
    first_window_end = manifest["effective"]["b002"]
    late = q(
        db,
        "SELECT order_id FROM raw.orders WHERE _batch_id = 'b003' AND order_date < ?",
        first_window_end,
    )
    assert len(late) >= manifest["expected"]["late_new_orders"]
    asof = elt.reconcile(built[1])
    assert asof["orders_with_wrong_point_in_time_segment"] == 0
    rows = q(
        db,
        """SELECT f.order_id, f.segment, h.segment FROM fct_orders f JOIN dim_customer_history h
                    ON h.customer_id = f.customer_id AND f.order_date >= h.valid_from AND f.order_date < h.valid_to
                    WHERE f.order_id IN (SELECT order_id FROM raw.orders WHERE _batch_id = 'b003' AND order_date < ?)""",
        first_window_end,
    )
    assert rows and all(a == b for _, a, b in rows)


def test_current_state_report_is_a_separate_question(built):
    _, db, _, _ = built
    asof = dict(q(db, "SELECT segment, orders FROM revenue_by_segment"))
    cur = dict(q(db, "SELECT segment, orders FROM revenue_by_current_segment"))
    assert sum(asof.values()) == sum(cur.values()) and asof != cur


def test_repeated_loads_and_builds_change_nothing(built, landing):
    work, db, _, _ = built
    root, _ = landing
    before = (
        elt.table_hash(db, elt.FCT),
        q(db, "SELECT COUNT(*) FROM dim_customer_history")[0][0],
        q(db, "SELECT COUNT(*) FROM raw.orders")[0][0],
    )
    for _ in range(2):
        w = Warehouse(db)
        loads = w.load_landing(root / "landing")
        w.close()
        assert all(x["status"] == "skipped" for x in loads)
        assert elt.build(work, db)["success"]
    after = (
        elt.table_hash(db, elt.FCT),
        q(db, "SELECT COUNT(*) FROM dim_customer_history")[0][0],
        q(db, "SELECT COUNT(*) FROM raw.orders")[0][0],
    )
    assert before == after


@pytest.mark.parametrize("order", elt.ARRIVALS)
def test_incremental_equals_full_refresh_for_every_arrival_order(landing, tmp_path, order):
    root, _ = landing
    r = elt.arrival_scenario(tmp_path, root / "landing", order, seed=3)
    assert (
        r["final_build_passed"]
        and r["equal_to_full_refresh"]
        and r["reconcile_ok"]
        and r["wrong_segments"] == 0
    )
    if order == "customers one batch late":
        assert (
            r["interim_failed_builds"] >= 1
        )  # orders without their customers are stopped by the data tests, then repaired on arrival


def test_late_dimension_snapshot_reattributes_already_loaded_facts(landing, tmp_path):
    root, _ = landing
    land = root / "landing"
    db = tmp_path / "w.duckdb"
    w = Warehouse(db)
    for b in ("b001", "b002", "b003"):  # customers of b003 are held back
        meta = json.loads((land / b / "_meta.json").read_text())
        w.copy(
            "orders", land / b / "orders.csv", b, maxerror=5, effective_date=meta["effective_date"]
        )
        if b != "b003":
            w.copy(
                "customers",
                land / b / "customers.csv",
                b,
                maxerror=5,
                effective_date=meta["effective_date"],
            )
    w.close()
    assert elt.build(tmp_path / "w", db)["success"]
    stale = elt.table_hash(db, elt.FCT)
    w = Warehouse(db)
    meta = json.loads((land / "b003" / "_meta.json").read_text())
    w.copy(
        "customers",
        land / "b003" / "customers.csv",
        "b003",
        maxerror=5,
        effective_date=meta["effective_date"],
    )
    w.close()
    assert elt.build(tmp_path / "w", db)["success"]
    assert (
        elt.table_hash(db, elt.FCT) != stale
    )  # facts of customers who changed segment in window 3 were re-attributed
    assert elt.reconcile(db)["orders_with_wrong_point_in_time_segment"] == 0


def test_a_failing_data_test_fails_the_build(built, tmp_path):
    _, db, _, _ = built
    bad = tmp_path / "bad.duckdb"
    shutil.copy(db, bad)
    con = duckdb.connect(str(bad))
    # a newly loaded order for a customer that does not exist (incremental models only see new loads, not in-place edits)
    con.execute(
        "INSERT INTO raw.orders VALUES (999999, 99999, DATE '2024-06-01', 'completed', 10.00, 'b999', now() + INTERVAL 1 DAY, NULL)"
    )
    con.close()
    res = elt.build(tmp_path / "w", bad)
    assert (
        not res["success"]
        and res["by_status"].get("fail", 0) + res["by_status"].get("error", 0) >= 1
    )


# ---------- Terraform plan checks and cost model ----------


def plan_with(**over):
    after = {
        "publicly_accessible": False,
        "enhanced_vpc_routing": True,
        "base_capacity": 8,
        "max_capacity": 32,
    }
    ns = {"manage_admin_password": True, "kms_key_id": "k"}
    after.update(over.get("wg", {}))
    ns.update(over.get("ns", {}))
    rc = [
        {
            "type": "aws_redshiftserverless_workgroup",
            "name": "elt",
            "change": {"actions": ["create"], "after": after},
        },
        {
            "type": "aws_redshiftserverless_namespace",
            "name": "elt",
            "change": {"actions": ["create"], "after": ns},
        },
        {
            "type": "aws_kms_key",
            "name": "warehouse",
            "change": {"actions": ["create"], "after": {}},
        },
        {
            "type": "aws_s3_bucket_public_access_block",
            "name": "landing",
            "change": {"actions": ["create"], "after": {}},
        },
    ]
    if not over.get("no_limit"):
        rc.append(
            {
                "type": "aws_redshiftserverless_usage_limit",
                "name": "compute",
                "change": {"actions": ["create"], "after": {}},
            }
        )
    return {"resource_changes": rc}


def test_plan_checks_pass_a_good_plan_and_flag_each_weakness():
    assert check_plan(plan_with()) == []
    assert any("publicly" in f for f in check_plan(plan_with(wg={"publicly_accessible": True})))
    assert any(
        "VPC routing" in f for f in check_plan(plan_with(wg={"enhanced_vpc_routing": False}))
    )
    assert any("exceeds" in f for f in check_plan(plan_with(wg={"base_capacity": 256})))
    assert any("max capacity" in f for f in check_plan(plan_with(wg={"max_capacity": None})))
    assert any(
        "Secrets Manager" in f for f in check_plan(plan_with(ns={"manage_admin_password": False}))
    )
    assert any("usage limit" in f for f in check_plan(plan_with(no_limit=True)))
    assert check_plan({"resource_changes": []})


needs_tf = pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform not installed")


@needs_tf
def test_real_terraform_plan_is_clean_and_variants_are_caught():
    assert check_plan(pipeline.terraform_plan()) == []
    assert any(
        "publicly" in f
        for f in check_plan(pipeline.terraform_plan(["-var=publicly_accessible=true"]))
    )
    assert any(
        "exceeds" in f
        for f in check_plan(
            pipeline.terraform_plan(["-var=base_capacity_rpu=128", "-var=max_capacity_rpu=512"])
        )
    )


def test_cost_model_arithmetic_and_minimums():
    assert run_cost(8, 3600) == 3.0
    assert run_cost(8, 10) == run_cost(8, 60)  # 60 s minimum billing
    assert monthly_cost(8, 4, 300)["rpu_hours"] == 80.0
    with pytest.raises(ValueError):
        run_cost(4, 10)


def test_infrastructure_file_has_no_credentials_and_uses_mock_provider():
    tf = (Path(pipeline.INFRA) / "main.tf").read_text()
    assert 'access_key                  = "mock"' in tf and "AKIA" not in tf
