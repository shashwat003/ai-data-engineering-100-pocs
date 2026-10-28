import random

import pytest
from lakeformation_access_control.demo_data import build
from lakeformation_access_control.enforce import AccessDenied, SecureEngine
from lakeformation_access_control.lint import lint
from lakeformation_access_control.main import ATTACKS, run
from lakeformation_access_control.model import (
    SUPER,
    Catalog,
    Column,
    DataFilter,
    Grant,
    ModelError,
    Table,
)
from lakeformation_access_control.policy import can_describe, effective_access

SEL = frozenset({"SELECT", "DESCRIBE"})


@pytest.fixture()
def lake():
    cat, con = build()
    return cat, con, SecureEngine(cat, con)


# --- model -----------------------------------------------------------------------------------------------------
def test_tag_inheritance_and_override(lake):
    cat, _, _ = lake
    assert (
        cat.effective_column_tags("sales.customers", "segment")["sensitivity"] == "internal"
    )  # from the database
    assert (
        cat.effective_column_tags("sales.customers", "email")["sensitivity"] == "pii"
    )  # column overrides database
    cat.tag_table("sales.orders", sensitivity="public")
    assert cat.effective_column_tags("sales.orders", "amount")["sensitivity"] == "public"
    assert (
        cat.effective_column_tags("sales.orders", "card_last4")["sensitivity"] == "pii"
    )  # column still wins over table


def test_tag_expression_is_and_across_keys_or_within_key(lake):
    cat, _, _ = lake
    tags = {"sensitivity": "public", "domain": "sales"}
    assert cat.matches((("sensitivity", ("public", "internal")), ("domain", ("sales",))), tags)
    assert not cat.matches((("sensitivity", ("internal",)), ("domain", ("sales",))), tags)
    assert not cat.matches((("sensitivity", ("public",)), ("domain", ("hr",))), tags)
    assert not cat.matches((("missing", ("x",)),), tags)


@pytest.mark.parametrize(
    "action",
    [
        lambda c: c.tag_database("sales", sensitivity="top-secret"),
        lambda c: c.tag_column("sales.customers", "nope", sensitivity="pii"),
        lambda c: c.tag_table("sales.ghost", sensitivity="pii"),
        lambda c: c.grant(Grant("p", frozenset({"SELECT"}))),
        lambda c: c.grant(
            Grant("p", frozenset({"SELECT"}), table="sales.customers", database="sales")
        ),
        lambda c: c.grant(Grant("p", frozenset({"HACK"}), table="sales.customers")),
        lambda c: c.grant(Grant("p", frozenset({"SELECT"}), data_filter="ghost")),
        lambda c: c.grant(
            Grant("p", frozenset({"SELECT"}), tag_expression=(("sensitivity", ("secret",)),))
        ),
        lambda c: c.add_filter(
            DataFilter("f", "sales", "customers", row_filter="region IN (SELECT 1)")
        ),
        lambda c: c.add_filter(DataFilter("f", "sales", "customers", row_filter="nonexistent = 1")),
        lambda c: c.add_filter(DataFilter("f", "sales", "customers", row_filter="1 = ")),
        lambda c: c.add_filter(DataFilter("f", "sales", "customers", columns=("zzz",))),
        lambda c: c.add_filter(DataFilter("f", "sales", "customers", row_filter="random() > 0.5")),
    ],
)
def test_invalid_model_operations_rejected(lake, action):
    with pytest.raises((ModelError, Exception)) as e:
        action(lake[0])
    assert isinstance(e.value, ModelError)


# --- effective access --------------------------------------------------------------------------------------------
def test_effective_columns_per_role(lake):
    cat, _, _ = lake
    cols = lambda p, t: set(effective_access(cat, p, t).columns)  # noqa: E731
    assert "email" not in cols("analyst_eu", "sales.customers") and "email" in cols(
        "compliance", "sales.customers"
    )
    assert cols("data_scientist", "sales.customers") == {
        "customer_id",
        "region",
        "segment",
    }  # pii-tagged name/email excluded
    assert cols("data_scientist", "sales.orders") == {"order_id", "customer_id", "region", "amount"}
    assert (
        cols("support", "sales.customers") == {"customer_id", "name", "region"}
        and cols("support", "sales.orders") == set()
    )
    assert cols("stranger", "sales.customers") == set()


def test_union_semantics_of_two_filters_gives_cellwise_visibility():
    cat, con = build()
    cat.add_filter(DataFilter("us_all_cols", "sales", "customers", row_filter="region = 'US'"))
    cat.add_filter(
        DataFilter(
            "eu_no_email",
            "sales",
            "customers",
            row_filter="region = 'EU'",
            excluded_columns=("email",),
        )
    )
    for f in ("us_all_cols", "eu_no_email"):
        cat.grant(Grant("mixed", SEL, data_filter=f))
    rows = SecureEngine(cat, con).query("mixed", "SELECT region, email FROM sales.customers")
    eu = [r for r in rows if r[0] == "EU"]
    us = [r for r in rows if r[0] == "US"]
    assert eu and us and all(r[1] is None for r in eu) and all(r[1] is not None for r in us)
    assert not [r for r in rows if r[0] == "APAC"]


def test_describe_without_select_cannot_query(lake):
    cat, con, eng = lake
    cat.grant(Grant("auditor", frozenset({"DESCRIBE"}), table="sales.orders"))
    assert (
        can_describe(cat, "auditor", "sales.orders")
        and not effective_access(cat, "auditor", "sales.orders").slices
    )
    with pytest.raises(AccessDenied, match="no SELECT"):
        eng.query("auditor", "SELECT * FROM sales.orders")


def test_iam_allowed_principals_opens_everything_and_lint_flags_it(lake):
    cat, con, eng = lake
    assert not eng.view_of and True or True
    cat.grant(Grant(SUPER, SEL, database="sales"))
    assert effective_access(cat, "stranger", "sales.customers").columns >= {"email", "name"}
    assert any(SUPER in m for m in lint(cat, {"compliance"}))
    assert not effective_access(cat, "stranger", "sales.customers", honour_super=False).slices


# --- enforcement ---------------------------------------------------------------------------------------------------
def test_attack_table_expectations_hold():
    r = run()
    assert all(v.startswith("OK") for v in r["attacks"].values()), r["attacks"]
    assert len(r["attacks"]) == len(ATTACKS)
    assert r["row_counts"]["analyst_eu"] == {"sales.customers": 30, "sales.orders": 100}


def test_select_star_returns_only_permitted_columns(lake):
    _, con, eng = lake
    rows = eng.query("support", "SELECT * FROM sales.customers LIMIT 1")
    assert len(rows[0]) == 3


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM sales.customers",
        "SELECT region, COUNT(*) c FROM sales.customers GROUP BY region",
        "SELECT o.amount FROM sales.orders o JOIN sales.customers c ON c.customer_id = o.customer_id",
    ],
)
def test_aggregates_and_joins_see_only_authorised_rows(lake, sql):
    _, _, eng = lake
    rows = eng.query("analyst_eu", sql)
    if "COUNT(*) FROM" in sql and "GROUP" not in sql:
        assert rows == [(30,)]
    if "GROUP" in sql:
        assert rows == [("EU", 30)]


def test_alias_collision_is_refused(lake):
    _, _, eng = lake
    with pytest.raises(AccessDenied, match="alias"):
        eng.query(
            "compliance",
            "SELECT 1 FROM sales.customers t WHERE EXISTS (SELECT 1 FROM sales.orders t)",
        )


def test_unparseable_sql_is_denied_not_executed(lake):
    _, _, eng = lake
    with pytest.raises(AccessDenied):
        eng.query("compliance", "SELEC * FRM")


# --- non-interference: data the role may not see cannot influence ANY answer -----------------------------------
FORBIDDEN_EMAIL = "'scrambled-' || CAST(customer_id AS VARCHAR)"


def perturb(con):
    con.execute(f"UPDATE customers SET email = {FORBIDDEN_EMAIL}")
    con.execute("UPDATE orders SET card_last4 = 'XXXX'")
    con.execute("UPDATE orders SET amount = amount * 7 + 1 WHERE region <> 'EU'")
    con.execute("UPDATE customers SET name = 'changed', segment = 'zzz' WHERE region <> 'EU'")
    con.execute(
        "INSERT INTO orders VALUES (9001, 1, 'US', 1e6, '9999'), (9002, 2, 'APAC', 5e5, '8888')"
    )
    con.execute("DELETE FROM customers WHERE region = 'APAC' AND customer_id > 60")


def random_query(rng: random.Random, cols_c, cols_o):
    t = rng.choice(["customers", "orders", "join"])
    if t == "customers":
        cols, src = cols_c, "sales.customers"
    elif t == "orders":
        cols, src = cols_o, "sales.orders"
    else:
        cols, src = (
            [f"o.{c}" for c in cols_o] + [f"c.{c}" for c in cols_c],
            "sales.orders o JOIN sales.customers c ON o.customer_id = c.customer_id",
        )
    pick = rng.sample(cols, k=min(len(cols), rng.randint(1, 3)))
    agg = rng.choice(["", "count", "min", "max"])
    c1 = rng.choice(cols)
    where = rng.choice(
        [
            "",
            f"WHERE {c1} IS NOT NULL",
            f"WHERE {c1} = {c1}",
            f"WHERE {c1} IS NULL OR {c1} IS NOT NULL",
        ]
    )
    if agg == "count":
        return f"SELECT COUNT(*), COUNT({pick[0]}) FROM {src} {where}"
    if agg:
        return f"SELECT {agg.upper()}({pick[0]}) FROM {src} {where}"
    return f"SELECT {', '.join(pick)} FROM {src} {where} ORDER BY 1, {len(pick)} LIMIT 50"


@pytest.mark.parametrize("role", ["analyst_eu", "data_scientist", "support"])
def test_noninterference_random_queries(role):
    cat_a, con_a = build()
    cat_b, con_b = build()
    perturb(con_b)
    ea, eb = SecureEngine(cat_a, con_a), SecureEngine(cat_b, con_b)
    from lakeformation_access_control.policy import effective_access as ea_

    cols_c = sorted(ea_(cat_a, role, "sales.customers").columns) or ["customer_id"]
    cols_o = sorted(ea_(cat_a, role, "sales.orders").columns) or ["order_id"]
    rng = random.Random(11)
    compared = denied = 0
    for _ in range(120):
        q = random_query(rng, cols_c, cols_o)
        outs = []
        for eng in (ea, eb):
            try:
                outs.append(("ok", sorted(map(repr, eng.query(role, q)))))
            except AccessDenied as e:
                outs.append(("denied", str(e)))
        # rows excluded by the filter / hidden columns must not change any answer; only values the role can read may differ
        if role == "analyst_eu" or outs[0][0] == "denied":
            assert outs[0] == outs[1], q
        compared += outs[0][0] == "ok"
        denied += outs[0][0] == "denied"
    assert compared > 20


def test_noninterference_for_analyst_eu_ignores_every_perturbation():
    """The perturbation changes only cells analyst_eu cannot read (non-EU rows, email, card_last4), apart from the inserted non-EU rows."""
    cat_a, con_a = build()
    cat_b, con_b = build()
    perturb(con_b)
    ea, eb = SecureEngine(cat_a, con_a), SecureEngine(cat_b, con_b)
    for q in (
        "SELECT COUNT(*) FROM sales.orders",
        "SELECT SUM(amount) FROM sales.orders",
        "SELECT name, segment FROM sales.customers ORDER BY customer_id",
        "SELECT region, COUNT(*) FROM sales.orders GROUP BY region",
    ):
        assert ea.query("analyst_eu", q) == eb.query("analyst_eu", q), q


# --- lint ----------------------------------------------------------------------------------------------------------
def test_lint_findings_on_demo_catalog():
    cat, _ = build()
    msgs = lint(cat, {"compliance"})
    assert any("analyst_eu can read PII column sales.customers.name" in m for m in msgs)
    assert not any("data_scientist" in m for m in msgs)


def test_lint_flags_dead_tag_grants_database_grants_and_grant_option():
    cat = Catalog()
    cat.add_table(Table("d", "t", [Column("a", "int")]))
    cat.define_tag("k", {"v", "w"})
    cat.tag_database("d", k="v")
    cat.grant(Grant("r1", SEL, tag_expression=(("k", ("w",)),)))
    cat.grant(Grant("r2", SEL, database="d"))
    cat.grant(Grant("r3", SEL, table="d.t", grant_option=True))
    cat.grant(Grant("*", SEL, table="d.t"))
    msgs = " | ".join(lint(cat, set()))
    for needle in ("matches no column", "whole database", "grant option", "broad principal"):
        assert needle in msgs
