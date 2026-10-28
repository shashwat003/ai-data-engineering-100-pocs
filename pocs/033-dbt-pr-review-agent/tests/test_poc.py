import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from dbt_pr_review_agent.dbt_runner import BASE, make_head
from dbt_pr_review_agent.project import children, load
from dbt_pr_review_agent.review import comment_payload, norm, review
from dbt_pr_review_agent.scenarios import SCENARIOS, sub

BASE_M = load(BASE)


def agent(name):
    with tempfile.TemporaryDirectory() as t:
        head = load(make_head(name, Path(t) / "p"))
    return review(BASE_M, head)


def codes(fs):
    return {f.code for f in fs}


def test_base_project_is_understood():
    assert set(BASE_M) == {
        "stg_orders",
        "stg_customers",
        "dim_customers",
        "fct_orders",
        "mart_revenue_daily",
    }
    assert BASE_M["fct_orders"].columns == [
        "order_id",
        "customer_id",
        "order_date",
        "status",
        "amount",
        "customer_segment",
    ]
    assert BASE_M["mart_revenue_daily"].group_by == ["customer_segment", "order_date"]
    assert (
        BASE_M["fct_orders"].joins == [("inner", "dim_customers")]
        and "cancelled" in BASE_M["fct_orders"].where
    )
    assert children(BASE_M)["dim_customers"] == ["fct_orders"]
    assert "order_id:unique" in BASE_M["fct_orders"].tests


def test_unchanged_project_has_no_findings():
    assert review(BASE_M, load(BASE)) == []


def test_every_scenario_actually_edits_something():
    for name, patch in SCENARIOS.items():
        assert patch, name
        for rel, content in patch.items():
            assert (
                content is None or not (BASE / rel).exists() or content != (BASE / rel).read_text()
            ), name


def test_breaking_column_removal_is_a_blocker_anchored_to_the_file():
    fs = agent("S1_drop_column_used_downstream")
    f = next(x for x in fs if x.code == "removed_column_used_downstream")
    assert (
        f.severity == "blocker"
        and f.model == "dim_customers"
        and f.file == "models/dim_customers.sql"
        and "fct_orders" in f.message
        and f.line >= 1
    )
    assert comment_payload(fs)["event"] == "request_changes"


def test_formatting_only_change_is_approved_silently():
    fs = agent("S5_formatting_only")
    assert fs == [] and comment_payload(fs)["event"] == "approve"


def test_each_semantic_change_is_detected_with_the_right_code():
    expect = {
        "S2_inner_to_left_join": "join_change", "S3_new_model_without_docs_or_tests": "new_model_no_tests", "S4_grain_change": "grain_change",
        "S6_materialize_as_table": "materialization_change", "S7_hardcoded_relation": "hardcoded_relation", "S9_test_removed": "test_removed", "S10_filter_removed": "filter_change",
    }  # fmt: skip
    for name, code in expect.items():
        assert code in codes(agent(name)), name


def test_instruction_in_sql_comment_is_flagged_and_does_not_suppress_findings():
    fs = agent("S8_breaking_change_plus_injection")
    assert {"suspicious_comment", "removed_column_used_downstream"} <= codes(fs)
    assert comment_payload(fs)["event"] == "request_changes"


def test_info_findings_are_not_posted_as_comments():
    p = comment_payload(agent("S6_materialize_as_table"))
    assert p["comments"] == [] and p["event"] == "comment"


def test_model_deletion_is_blocker_when_referenced(tmp_path):
    proj = tmp_path / "p"
    shutil.copytree(BASE, proj)
    (proj / "models" / "dim_customers.sql").unlink()
    with pytest.raises(ValueError, match="unresolved"):
        load(proj)  # fct_orders refs a model that no longer exists: the project itself is broken


def test_removing_a_leaf_model_is_only_a_warning(tmp_path):
    proj = tmp_path / "p"
    shutil.copytree(BASE, proj)
    (proj / "models" / "mart_revenue_daily.sql").unlink()
    fs = review(BASE_M, load(proj))
    assert [(f.code, f.severity) for f in fs] == [("model_removed", "warn")]


def test_sub_helper_refuses_noop_patches():
    with pytest.raises(ValueError):
        sub("abc", "zzz", "y")


def test_norm_ignores_comments_case_and_whitespace_but_not_logic():
    h = load_variant("select a , b from t  -- c", "SELECT a,b FROM t")
    assert norm(h[0]) == norm(h[1])
    h2 = load_variant("select a from t where a > 1", "select a from t where a > 2")
    assert norm(h2[0]) != norm(h2[1])


def load_variant(sql_a, sql_b):
    out = []
    for i, sql in enumerate((sql_a, sql_b)):
        d = Path(tempfile.mkdtemp()) / f"p{i}"
        (d / "models").mkdir(parents=True)
        (d / "seeds").mkdir()
        (d / "models" / "m.sql").write_text(sql)
        out.append(load(d)["m"])
    return out


def test_unparseable_sql_is_a_blocker(tmp_path):
    proj = tmp_path / "p"
    shutil.copytree(BASE, proj)
    (proj / "models" / "stg_orders.sql").write_text(
        "select from from {{ ref('raw_orders') }} where"
    )
    fs = review(BASE_M, load(proj))
    assert any(f.code == "sql_parse_error" and f.severity == "blocker" for f in fs)


# ---- ground truth with real dbt (needs dbt-core + dbt-duckdb: heavy workflow)

pytestmark_dbt = pytest.mark.skipif(
    shutil.which("dbt") is None and not Path(sys.executable).with_name("dbt").exists(),
    reason="dbt not installed",
)


@pytestmark_dbt
def test_agent_risk_flags_agree_with_real_dbt_outcomes():
    from dbt_pr_review_agent.main import run

    res = run()
    assert res["base_build_ok"]
    c = res["confusion"]
    assert c["fn"] == 0 and c["fp"] <= 1 and c["tp"] >= 5
    by = {r["scenario"]: r for r in res["scenarios"]}
    assert (
        not by["S1_drop_column_used_downstream"]["dbt_ok"]
        and by["S1_drop_column_used_downstream"]["agent_flags_risk"]
    )
    assert (
        by["S5_formatting_only"]["dbt_ok"]
        and not by["S5_formatting_only"]["truth_risky"]
        and not by["S5_formatting_only"]["agent_flags_risk"]
    )
    assert (
        by["S2_inner_to_left_join"]["data_changed_models"] and by["S2_inner_to_left_join"]["dbt_ok"]
    )
