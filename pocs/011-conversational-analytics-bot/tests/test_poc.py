import pytest
from conversational_analytics_bot import tools
from conversational_analytics_bot.main import ANALYST, MANAGER, load_duckdb, run
from conversational_analytics_bot.planner import plan
from conversational_analytics_bot.tools import Refusal, User, compile_sql, run_tool, validate

CON = load_duckdb()
R = run()


def ask(call, user=ANALYST):
    audit = []
    return run_tool(CON, call, user, audit), audit


def test_governed_answers_match_independent_sql():
    assert R["truth_checked"] == R["truth_correct"] == 4
    assert all(g["ok"] for g in R["good"])


def test_all_adversarial_prompts_are_refused_and_tables_survive():
    assert all(a["refused"] for a in R["adversarial"]) and R["tables_intact"]


@pytest.mark.parametrize(
    "call",
    [
        {"tool": "run_sql", "sql": "SELECT 1"},
        {"tool": "query_metric", "metric": "revenue; DROP TABLE orders"},
        {"tool": "query_metric", "metric": "revenue", "group_by": ["email"]},
        {"tool": "query_metric", "metric": "revenue", "group_by": ["c.email"]},
        {"tool": "query_metric", "metric": "revenue", "group_by": ["country", "segment", "month"]},
        {
            "tool": "query_metric",
            "metric": "revenue",
            "filters": [{"dim": "country", "op": "LIKE", "value": "%"}],
        },
        {
            "tool": "query_metric",
            "metric": "revenue",
            "filters": [{"dim": "country", "op": "eq", "value": "x' OR '1'='1"}],
        },
        {
            "tool": "query_metric",
            "metric": "revenue",
            "filters": [{"dim": "year", "op": "eq", "value": "2024 OR 1=1"}],
        },
        {
            "tool": "query_metric",
            "metric": "revenue",
            "filters": [{"dim": "email", "op": "eq", "value": "a"}],
        },
        {
            "tool": "query_metric",
            "metric": "revenue",
            "filters": [{"dim": "country", "op": "in", "value": list("abcdefghijk")}],
        },
        "not even a dict",
        {},
    ],
)
def test_hostile_or_malformed_calls_are_refused(call):
    a, audit = ask(call)
    assert not a.ok and audit[0]["outcome"].startswith("refused")


def test_limit_is_clamped_not_trusted():
    assert validate({"tool": "query_metric", "metric": "revenue", "limit": 10**9})["limit"] == 50
    assert validate({"tool": "query_metric", "metric": "revenue", "limit": -5})["limit"] >= 1
    assert validate({"tool": "query_metric", "metric": "revenue", "limit": "abc"})["limit"] == 50


def test_user_values_are_bound_parameters_never_sql_text():
    call = validate(
        {
            "tool": "query_metric",
            "metric": "revenue",
            "filters": [{"dim": "country", "op": "eq", "value": "Japan"}],
        }
    )
    sql, params = compile_sql(call, ANALYST)
    assert "Japan" not in sql and params[0] == "Japan"
    assert "?" in sql


def test_row_level_security_cannot_be_filtered_away():
    assert (
        R["rls_manager_sees_countries"] == ["Germany"] and R["rls_cross_country_filter_rows"] == 0
    )
    a, _ = ask(
        {
            "tool": "query_metric",
            "metric": "revenue",
            "filters": [{"dim": "country", "op": "in", "value": ["Germany", "Japan"]}],
            "group_by": ["country"],
        },
        MANAGER,
    )
    assert {r[0] for r in a.rows} == {"Germany"}


def test_manager_without_country_is_refused():
    a, _ = ask({"tool": "query_metric", "metric": "revenue"}, User("x", "manager", None))
    assert not a.ok and "country" in a.message


def test_small_groups_are_suppressed(monkeypatch):
    monkeypatch.setattr(tools, "MIN_GROUP", 10_000)
    a, _ = ask({"tool": "query_metric", "metric": "revenue", "group_by": ["country"]})
    assert a.ok and a.rows == [] and a.suppressed == 8
    a, _ = ask({"tool": "query_metric", "metric": "revenue"})
    assert a.rows == [] and a.suppressed == 1


def test_internal_count_column_is_not_leaked():
    a, _ = ask({"tool": "query_metric", "metric": "revenue", "group_by": ["country"]})
    assert a.columns == ["country", "revenue"] and all(len(r) == 2 for r in a.rows)


def test_audit_records_refusals_and_sql_but_no_result_values():
    audit = []
    run_tool(CON, plan("revenue by country"), ANALYST, audit)
    run_tool(CON, plan("select email from customers"), ANALYST, audit)
    assert audit[0]["outcome"].startswith("ok") and "sql" in audit[0]
    assert audit[1]["outcome"].startswith("refused")
    assert "@" not in str(audit)


def test_describe_metrics_lists_definitions():
    a, _ = ask({"tool": "describe_metrics"})
    assert a.ok and "revenue" in a.message and "completed or shipped" in a.message


def test_validate_rejects_unknown_tool_directly():
    with pytest.raises(Refusal):
        validate({"tool": "shell"})


def test_planner_is_deterministic_and_maps_known_phrasing():
    assert plan("top 3 revenue by country") == plan("top 3 revenue by country")
    c = plan("revenue by category in 2024")
    assert c["group_by"] == ["category"] and c["filters"][0] == {
        "dim": "year",
        "op": "eq",
        "value": "2024",
    }
