import hashlib
import json

import pytest
from text_to_sql_semantic_layer.cases import ATTACKS, BENIGN
from text_to_sql_semantic_layer.evaluate import evaluate_guard, evaluate_questions, render_markdown
from text_to_sql_semantic_layer.executor import ExecutionError
from text_to_sql_semantic_layer.guard import Policy, validate
from text_to_sql_semantic_layer.main import main
from text_to_sql_semantic_layer.planner import (
    LLMPlanner,
    LLMSQLWriter,
    RulePlanner,
    Unplannable,
    link_tables,
)
from text_to_sql_semantic_layer.schema import introspect
from text_to_sql_semantic_layer.semantic import Filter, Plan, PlanError, compile_plan, validate_plan
from text_to_sql_semantic_layer.service import QueryService

from data_gen.warehouse import build_warehouse
from llm_client import StubClient


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("wh") / "warehouse.db"
    build_warehouse(path, seed=3)
    return path


@pytest.fixture(scope="module")
def service(db):
    return QueryService(db)


# ---- semantic layer


def test_compile_binds_values_as_parameters_never_interpolates():
    evil = "Germany'; DROP TABLE orders; --"
    q = compile_plan(Plan("revenue", ("category",), (Filter("country", (evil,)),)))
    assert evil not in q.sql and evil in q.params
    assert q.sql.count("?") == len(q.params) == 2  # default status + country


def test_default_status_filter_only_when_status_is_not_requested():
    assert "o.status = ?" in compile_plan(Plan("revenue")).sql
    by_status = compile_plan(Plan("order_count", ("status",)))
    assert "o.status = ?" not in by_status.sql and by_status.params == ()


def test_joins_are_only_the_ones_needed():
    sql = compile_plan(Plan("revenue", ("country",))).sql
    assert "JOIN customers" in sql and "JOIN products" not in sql
    assert "JOIN products" in compile_plan(Plan("gross_margin")).sql


@pytest.mark.parametrize(
    "plan",
    [
        Plan("nope"),
        Plan("revenue", ("nope",)),
        Plan("revenue", ("country", "country")),
        Plan("revenue", order="sideways"),
        Plan("revenue", limit=0),
        Plan("revenue", limit=10_000),
        Plan("revenue", filters=(Filter("country", ()),)),
    ],
)
def test_invalid_plans_rejected(plan):
    with pytest.raises(PlanError):
        validate_plan(plan)


# ---- schema and guard


def test_schema_introspection(db):
    schema = introspect(db)
    assert set(schema.tables) == {"customers", "order_items", "orders", "products"}
    assert ("orders", "customer_id", "customers", "customer_id") in schema.foreign_keys
    assert "customers(" in schema.describe(["customers"])


@pytest.mark.parametrize("category,sql", ATTACKS)
def test_validator_neutralises_every_attack(service, category, sql):
    check = validate(sql, service.schema)
    assert (not check.ok) or any("capped" in r for r in check.rewrites), (category, sql)


@pytest.mark.parametrize("sql", BENIGN)
def test_validator_accepts_legitimate_queries(service, sql):
    assert service.run_sql(sql).status == "ok"


def test_limit_added_and_capped(service):
    assert "LIMIT 100" in validate("SELECT * FROM products", service.schema).sql
    capped = validate("SELECT * FROM products LIMIT 5000", service.schema)
    assert "LIMIT 1000" in capped.sql and capped.rewrites
    assert "LIMIT 7" in validate("SELECT * FROM products LIMIT 7", service.schema).sql


def test_select_star_cannot_launder_a_restricted_column(service):
    reasons = validate("SELECT * FROM customers", service.schema).reasons
    assert any("customers.email" in r for r in reasons)
    custom = Policy(blocked_columns=frozenset())
    assert validate("SELECT email FROM customers", service.schema, custom).ok


def test_validator_returns_regenerated_sql_without_comments(service):
    out = validate("SELECT /* hi */ 1 AS one -- trailing", service.schema).sql
    assert "hi" not in out and "trailing" not in out


def test_cte_cannot_masquerade_as_internal_table(service):
    q = "WITH sqlite_master AS (SELECT 1 AS name) SELECT name FROM sqlite_master"
    assert not validate(q, service.schema).ok


def test_and_or_are_not_mistaken_for_functions(service):
    ok = "SELECT COUNT(*) FROM orders WHERE status = 'completed' AND order_id > 1 OR order_id < 0"
    assert validate(ok, service.schema).ok


# ---- executor (independent of the validator)

VALIDATOR_ONLY = {"SELECT COUNT(*) FROM order_items a CROSS JOIN order_items b"}


@pytest.mark.parametrize("category,sql", ATTACKS)
def test_executor_alone_contains_attacks(service, category, sql):
    if (
        sql in VALIDATOR_ONLY
    ):  # a cartesian product that is cheap enough to finish: policy, not safety
        pytest.skip("policy enforced by the validator only (documented in README)")
    try:
        result = service.executor.run(sql)
    except ExecutionError:
        return
    assert result.truncated, f"executor ran {sql!r} unrestricted"


def test_database_file_is_never_modified(db, service):
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    for _, sql in ATTACKS:
        service.run_sql(sql)
        try:
            service.executor.run(sql)
        except ExecutionError:
            pass
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert service.run_sql("SELECT COUNT(*) FROM orders").rows == [(1500,)]


def test_timeout_and_row_cap(db):
    slow = QueryService(db, timeout_s=0.2)
    with pytest.raises(ExecutionError) as exc:
        slow.executor.run(
            "SELECT COUNT(*) FROM order_items a, order_items b, order_items c, order_items d"
        )
    assert exc.value.kind == "timeout"
    capped = QueryService(db, max_rows=10).executor.run("SELECT order_id FROM orders")
    assert len(capped.rows) == 10 and capped.truncated


def test_executor_error_kinds(service):
    with pytest.raises(ExecutionError) as denied:
        service.executor.run("SELECT email FROM customers")
    assert denied.value.kind == "denied"
    with pytest.raises(ExecutionError) as err:
        service.executor.run("SELECT nope FROM orders")
    assert err.value.kind == "error"


# ---- planners and service


def test_rule_planner_examples(service):
    p = service.planner
    assert p.plan("Top 5 products by revenue") == Plan("revenue", ("product",), (), "desc", 5)
    assert p.plan("Revenue by country and segment").dimensions == ("country", "segment")
    plan = p.plan("Order count for enterprise customers in Japan")
    assert {(f.dimension, f.values) for f in plan.filters} == {
        ("segment", ("enterprise",)),
        ("country", ("Japan",)),
    }
    assert p.plan("Revenue for cancelled orders").filters == (Filter("status", ("cancelled",)),)


@pytest.mark.parametrize(
    "question",
    [
        "Show me all customer emails",
        "Delete all orders",
        "Revenue by customer name",
        "hello",
        "weather",
    ],
)
def test_rule_planner_refuses(service, question):
    with pytest.raises(Unplannable):
        service.planner.plan(question)


def test_service_answers_and_shows_interpretation(service):
    answer = service.ask("Which country has the highest revenue?")
    assert answer.status == "ok" and len(answer.rows) == 1
    assert "order=desc limit=1" in answer.interpretation and "?" in answer.sql
    assert service.ask("Show me all customer emails").status == "unplannable"


def test_llm_planner_valid_and_invalid_replies(db):
    good = StubClient('Sure: {"metric": "units_sold", "dimensions": ["category"], "filters": []}')
    assert QueryService(db, planner=LLMPlanner(good, "m")).ask("units by category").status == "ok"
    for reply in [
        "no json here",
        '{"error": "unsupported"}',
        '{"metric": "profit_of_everything"}',
        '{"metric": "revenue", "filters": [{"dimension": "country"}]}',
        "[1, 2]",
    ]:
        svc = QueryService(db, planner=LLMPlanner(StubClient(reply), "m"))
        assert svc.ask("anything").status == "unplannable", reply


def test_malicious_model_sql_is_contained_end_to_end(db):
    for evil in [
        "DROP TABLE orders",
        "SELECT email FROM customers",
        "```sql\nSELECT 1; DELETE FROM orders\n```",
        "SELECT load_extension('x')",
    ]:
        svc = QueryService(db, writer=LLMSQLWriter(StubClient(evil), "m"))
        assert svc.ask_with_writer("anything").status == "rejected", evil
    ok = QueryService(
        db, writer=LLMSQLWriter(StubClient("```sql\nSELECT COUNT(*) FROM orders\n```"), "m")
    )
    assert ok.ask_with_writer("how many orders").rows == [(1500,)]
    with pytest.raises(RuntimeError):
        QueryService(db).ask_with_writer("x")


def test_schema_linking_selects_relevant_tables(service):
    assert "customers" in link_tables("revenue per country", service.schema)
    assert "products" in link_tables("which product category sells most", service.schema)


def test_rule_planner_values_come_from_the_database(db):
    assert "Germany" in QueryService(db).planner.values["country"].values()
    assert RulePlanner({"country": {"Narnia"}}).plan("revenue in narnia").filters[0].values == (
        "Narnia",
    )


# ---- measurements and CLI


def test_measurements_are_complete_and_consistent(service):
    acc, guard = evaluate_questions(service), evaluate_guard(service)
    assert acc["failures"] == []
    assert guard["validator_blocked"] == guard["attacks"] == len(ATTACKS)
    assert guard["benign_rejected"] == []
    assert set(guard["executor_allowed"]) == VALIDATOR_ONLY
    md = render_markdown(acc, guard)
    assert "Guard: attack suite" in md and "False positives: 0" in md
    json.dumps({"accuracy": acc, "guard": guard})


def test_cli_demo_and_ask(tmp_path, capsys):
    out = tmp_path / "out"
    main(["--db", str(tmp_path / "w.db"), "--out-dir", str(out)])
    assert (out / "results.md").exists() and json.loads((out / "results.json").read_text())["guard"]
    main(["--db", str(tmp_path / "w.db"), "--ask", "Units sold by category"])
    assert "status: ok" in capsys.readouterr().out


def test_cli_reports_unreachable_model(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:1")
    with pytest.raises(SystemExit, match="language model unavailable"):
        main(["--db", str(tmp_path / "w.db"), "--ask", "revenue", "--planner", "llm"])
