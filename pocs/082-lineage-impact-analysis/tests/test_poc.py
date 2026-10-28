import pytest
from lineage_impact_analysis.lineage import (
    LineageError,
    breaking_change,
    build_graph,
    column_edges,
    impact,
    output_columns,
    schema_for,
    topo,
)
from lineage_impact_analysis.main import score
from lineage_impact_analysis.project import MODELS, RAW, TRUTH

EDGES = column_edges(MODELS, RAW)
G = build_graph(EDGES)


def test_every_labelled_column_matches_ground_truth_exactly():
    s = score(EDGES)
    assert s["precision"] == 1.0 and s["recall"] == 1.0 and s["wrong"] == [], s["wrong"]
    assert s["columns_checked"] == len(TRUTH) >= 20


def test_topological_order_puts_dependencies_first():
    order = topo(MODELS)
    pos = {m: i for i, m in enumerate(order)}
    assert (
        pos["staging.stg_items"]
        < pos["intermediate.int_order_margin"]
        < pos["marts.fct_orders"]
        < pos["marts.dim_customer"]
        < pos["marts.revenue_by_segment"]
        < pos["marts.exec_dashboard"]
    )


def test_output_columns_and_star_expansion():
    schema = schema_for(MODELS, RAW)
    assert output_columns("SELECT * FROM raw.customers", schema) == [
        "customer_id",
        "name",
        "email",
        "segment",
        "country",
    ]
    assert (
        output_columns(
            "SELECT o.*, c.name FROM raw.orders o JOIN raw.customers c USING (customer_id)", schema
        )[-1]
        == "name"
    )
    assert schema["marts"]["fct_orders"].keys() >= {"order_id", "margin", "order_seq"}


def test_transitive_impact_of_a_raw_column():
    i = impact(G, "raw.orders.amount")
    assert (
        "marts.dim_customer.lifetime_value" in i["columns"]
        and "marts.exec_dashboard.revenue" in i["columns"]
    )
    assert "marts.category_margin.margin" not in i["columns"]  # a different branch is untouched
    assert i["depth"] == 5 and "marts.exec_dashboard" in i["tables"]


def test_impact_of_pii_column_is_confined_to_the_domain_branch():
    i = impact(G, "raw.customers.email")
    assert i["tables"] == ["marts.email_domains", "staging.stg_customers"]


def test_impact_of_a_whole_table_covers_all_its_columns():
    assert set(impact(G, "staging.stg_items")["tables"]) >= {
        "intermediate.int_order_margin",
        "marts.category_margin",
        "marts.fct_orders",
    }
    assert impact(G, "raw.nothing.here") == {"columns": [], "tables": [], "depth": 0}


def test_window_function_lineage_includes_partition_and_order_columns():
    assert EDGES["marts.fct_orders.order_seq"] == {
        "staging.stg_orders.customer_id",
        "staging.stg_orders.order_date",
    }


def test_derived_expression_has_all_inputs_and_count_star_has_none():
    assert EDGES["staging.stg_items.line_cost"] == {
        "raw.order_items.quantity",
        "raw.products.unit_cost",
    }
    assert EDGES["marts.email_domains.customers"] == set()


def test_breaking_rename_is_detected_with_a_reason():
    new = dict(MODELS)
    new["staging.stg_orders"] = MODELS["staging.stg_orders"].replace("AS amount_usd", "AS amount")
    out = breaking_change(MODELS, new, RAW)
    assert out and "no longer compiles" in out[0] and "amount_usd" in out[0]


def test_unread_leaf_rename_is_not_breaking_but_changes_the_graph():
    new = dict(MODELS)
    new["marts.email_domains"] = MODELS["marts.email_domains"].replace(
        "COUNT(*) AS customers", "COUNT(*) AS n_customers"
    )
    assert breaking_change(MODELS, new, RAW) == []


def test_removed_output_column_that_downstream_reads_is_reported():
    new = dict(MODELS)
    new["staging.stg_customers"] = MODELS["staging.stg_customers"].replace("segment, ", "")
    out = breaking_change(MODELS, new, RAW)
    assert out and any("segment" in x for x in out)


def test_union_lineage_collects_both_branches():
    models = {
        "staging.u": "SELECT customer_id AS id FROM raw.customers UNION ALL SELECT order_id AS id FROM raw.orders"
    }
    e = column_edges(models, RAW)
    assert e["staging.u.id"] == {"raw.customers.customer_id", "raw.orders.order_id"}


def test_unknown_table_in_a_model_raises_instead_of_guessing():
    with pytest.raises(LineageError, match="unknown table raw.missing_table"):
        column_edges({"staging.bad": "SELECT x FROM raw.missing_table"}, RAW)


def test_scales_to_a_long_chain_of_models():
    models, raw = {}, {"raw": {"t": {"a": "int"}}}
    prev = "raw.t"
    for i in range(60):
        name = f"staging.m{i}"
        models[name] = f"SELECT a + 1 AS a FROM {prev}"
        prev = name
    e = column_edges(models, raw)
    g = build_graph(e)
    assert impact(g, "raw.t.a")["depth"] == 60
