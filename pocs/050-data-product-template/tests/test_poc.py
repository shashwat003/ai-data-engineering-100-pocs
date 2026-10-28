from datetime import date, datetime, timedelta

import pytest
import yaml
from data_contracts_ci_gate.contract import parse_contract
from data_product_template import main as m
from data_product_template.cli import main as cli
from data_product_template.cli import validate
from data_product_template.dbtgen import to_dbt_schema
from data_product_template.product import ProductError, load_product, parse_product
from data_product_template.registry import check_registry
from data_product_template.scaffold import new_product
from data_product_template.slo import evaluate


@pytest.fixture()
def root(tmp_path):
    return new_product("order_events", "sales", "sales-data", "oncall@example.com", tmp_path)


def test_scaffold_valid_and_refuses_overwrite(root, tmp_path):
    assert validate(root) == []
    assert (root / "dbt_project" / "models" / "schema.yml").exists()
    with pytest.raises(FileExistsError):
        new_product("order_events", "sales", "sales-data", "o@example.com", tmp_path)


@pytest.mark.parametrize(
    "name,owner",
    [("Bad-Name", "ok"), ("ok_name", "{{ 7*7 }}"), ("ab", "ok"), ("ok_name", "x" * 80)],
)
def test_scaffold_rejects_unsafe_input(tmp_path, name, owner):
    with pytest.raises(ValueError):
        new_product(name, "d", owner, "o@example.com", tmp_path)


def test_generated_schema_matches_contract(root):
    schema = yaml.safe_load((root / "dbt_project" / "models" / "schema.yml").read_text())
    model = schema["models"][0]
    assert model["config"]["contract"]["enforced"] is True
    cols = {c["name"]: c for c in model["columns"]}
    assert cols["id"]["data_tests"] == ["not_null", "unique"]
    assert cols["amount"]["data_type"] == "double" and cols["contact_email"]["meta"]["pii"] is True
    assert (root / "dbt_project" / "tests" / "assert_order_events_amount_valid.sql").exists()


def test_cli_detects_schema_drift(root):
    assert cli(["dbt-schema", str(root), "--check"]) == 0
    c = root / "contract.yaml"
    c.write_text(c.read_text().replace("max: 1000000", "max: 5000"))
    assert cli(["dbt-schema", str(root), "--check"]) == 1
    assert cli(["dbt-schema", str(root)]) == 0 and cli(["dbt-schema", str(root), "--check"]) == 0


def test_manifest_rules(root):
    text = (root / "product.yaml").read_text()
    for bad in (text.replace("tier: silver", "tier: gold"), text.replace("status: draft", "status: deprecated"), text.replace("classification: internal", "classification: restricted"),
                text + "extra: 1\n", text.replace("freshness_hours: 26", "freshness_hours: -3"), text.replace("retention_days: 730", "retention_days: true")):  # fmt: skip
        with pytest.raises(ProductError):
            parse_product(bad)
    with pytest.raises(ProductError):
        parse_product("[1,2]")


def test_validate_catches_cross_file_inconsistency(root):
    c = root / "contract.yaml"
    c.write_text(c.read_text().replace("owner: sales-data", "owner: someone-else"))
    assert any("owner" in e for e in validate(root))
    p = root / "product.yaml"
    p.write_text(p.read_text().replace("classification: internal", "classification: public"))
    assert any("PII" in e for e in validate(root))


def _con(loads, rows=20, tmp_path=None):
    import duckdb

    con = duckdb.connect()
    con.execute(
        "create table order_events(id integer, event_date date, status varchar, amount double, contact_email varchar)"
    )
    for i in range(1, rows + 1):
        con.execute(
            "insert into order_events values (?,?,?,?,?)",
            [i, date(2026, 3, 9), "new", 10.0, f"u{i}@example.com"],
        )
    con.execute("create table _loads(load_ts timestamp, ok boolean, rows bigint)")
    if loads:
        con.executemany("insert into _loads values (?,?,?)", loads)
    return con


def test_slo_all_met_and_each_breach(root):
    product = load_product(root / "product.yaml")
    contract = parse_contract((root / "contract.yaml").read_text())
    now = datetime(2026, 3, 10, 12)
    good = m.daily(30, 20)
    r = evaluate(_con(good), product, contract, now)
    assert r["all_met"], r
    assert (
        evaluate(_con(m.daily(30, 20, end=now - timedelta(days=3))), product, contract, now)[
            "slis"
        ][0]["met"]
        is False
    )
    assert (
        evaluate(_con(m.daily(30, 20, last_rows=3)), product, contract, now)["slis"][3]["met"]
        is False
    )
    burn = evaluate(_con(m.daily(30, 20, fail_days=(1, 2, 3, 4))), product, contract, now)
    assert burn["error_budget"]["exhausted"] and not burn["slis"][4]["met"]
    con = _con(good)
    con.execute("update order_events set amount = null where id <= 5")
    assert not evaluate(con, product, contract, now)["slis"][1]["met"]


def test_slo_empty_table_and_no_loads_fail_closed(root):
    product = load_product(root / "product.yaml")
    contract = parse_contract((root / "contract.yaml").read_text())
    r = evaluate(_con([], rows=0), product, contract, datetime(2026, 3, 10))
    assert not r["all_met"] and all(not s["met"] for s in r["slis"])


def _p(name, status="active", consumers=(), sunset=None, out=None):
    t = f"""product: {name}
domain: d
owner: {{team: t, oncall: o@example.com}}
tier: silver
status: {status}
{"sunset_date: " + sunset if sunset else ""}
output: {{table: {out or name}}}
contract: c.yaml
slos: {{freshness_hours: 24, completeness_min: 0.9, validity_min: 0.9, volume_tolerance_pct: 50, availability_target: 0.9, window_days: 30}}
access: {{classification: internal, allowed_roles: [analyst]}}
retention_days: 100
consumers: {list(consumers)}
"""
    return parse_product(t)


def test_registry_checks():
    today = date(2026, 3, 10)
    assert check_registry([_p("aaa"), _p("bbb", consumers=["aaa"])], today) == []
    assert any(
        "not a registered" in x for x in check_registry([_p("aaa", consumers=["ghost"])], today)
    )
    assert any(
        "already owned" in x
        for x in check_registry([_p("aaa", out="t"), _p("bbb", out="t")], today)
    )
    assert any(
        "past sunset" in x
        for x in check_registry([_p("old", "deprecated", ["new"], "2026-01-01"), _p("new")], today)
    )
    assert any(
        "depends on draft" in x
        for x in check_registry([_p("aaa", consumers=["bbb"]), _p("bbb", "draft")], today)
    )


def test_evolution_gate_through_demo_labels(root):
    from data_contracts_ci_gate.evolution import check_evolution

    old = parse_contract((root / "contract.yaml").read_text())
    new = parse_contract((root / "contract.yaml").read_text().replace("max: 1000000", "max: 5000"))
    assert not check_evolution(old, new).ok  # tightening a bound without a major bump


def test_to_dbt_schema_deterministic(root):
    p = load_product(root / "product.yaml")
    c = parse_contract((root / "contract.yaml").read_text())
    assert to_dbt_schema(c, p) == to_dbt_schema(c, p)


def test_real_dbt_enforces_contract_and_tests():
    r = m.run()
    assert r["dbt_clean_build_ok"] is True
    assert r["dbt_defect_builds_ok"] == {"negative_amount": False, "bad_status": False}
    assert r["schema_drift_detected"] is True
    by = {row[0]: row for row in r["slo_scenarios"]}
    assert by["healthy"][1] is True and all(
        row[1] is False for k, row in by.items() if k != "healthy"
    )
