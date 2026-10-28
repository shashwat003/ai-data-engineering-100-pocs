import pandas as pd
import pytest
from multi_agent_dq_fixer import agents
from multi_agent_dq_fixer.agents import State, _closest, apply_op, run_pipeline, verifier_and_gate
from multi_agent_dq_fixer.checks import run_checks
from multi_agent_dq_fixer.data import INJECTION_TEXT, make
from multi_agent_dq_fixer.main import CHECK_TO_TRUTH, run

D = make()
S = run_pipeline(D["orders"], D["customers"])
R = run()


def test_every_injected_defect_is_found_exactly():
    found = {CHECK_TO_TRUTH[(i.check, i.column)]: set(i.rows) for i in S.issues}
    for k, rows in D["truth"].items():
        if k != "hostile_note":
            assert found[k] == set(rows), k
    assert len(S.issues) == 6


def test_clean_data_raises_nothing():
    s = run_pipeline(D["clean"], D["customers"])
    assert (
        s.issues == [] and s.proposals == [] and s.applied == [] and len(s.clean) == len(D["clean"])
    )


def test_safe_fixes_are_auto_applied_and_correct():
    assert {(a["op"], a["issue"]) for a in S.applied} == {
        ("drop_duplicates", ("unique", "order_id")),
        ("map_values", ("in_set", "status")),
    }
    assert R["status_fixed_correctly"] == R["status_typos"] == 40
    assert S.clean.order_id.is_unique and len(S.clean) == len(D["clean"])


def test_unrepairable_issues_are_queued_not_guessed():
    q = {r["issue"] for r in S.queued}
    assert q == {
        ("not_null", "customer_id"),
        ("range_min", "amount"),
        ("not_future", "order_date"),
        ("fk_exists", "customer_id"),
    }
    assert all(r["op"] == "quarantine" and r["decision"] == "queued_for_human" for r in S.queued)
    # rows are still present and unchanged: nothing was invented
    assert S.clean.customer_id.isna().sum() == 30 and (S.clean.amount < 0).sum() == 15


def test_no_unrelated_column_or_row_changed():
    out = S.clean.set_index("order_id").sort_index()
    ref = D["clean"].set_index("order_id").sort_index()
    for c in ("amount", "order_date", "email", "note"):
        same = out[c].equals(ref[c])
        if c in ("amount", "order_date"):  # injected defects remain by design
            assert (out[c] != ref[c]).sum() == len(
                D["truth"]["negative_amount"] if c == "amount" else D["truth"]["future_date"]
            )
        elif c == "note":
            assert (out[c] != ref[c]).sum() == 1
        else:
            assert same


def test_second_run_is_idempotent():
    assert R["second_run_auto_applied"] == 0
    s2 = run_pipeline(S.clean, D["customers"])
    assert s2.clean.equals(S.clean) or len(s2.clean) == len(S.clean)


def test_hostile_text_in_data_has_no_effect():
    assert INJECTION_TEXT in set(S.clean.note) and INJECTION_TEXT not in str(S.trace)
    assert len(S.clean) == len(
        D["clean"]
    )  # no table dropped, nothing "marked as fixed" by the text


def test_gate_respects_confidence_and_fraction_thresholds(monkeypatch):
    monkeypatch.setattr(agents, "AUTO_CONF", 0.999)
    s = run_pipeline(D["orders"], D["customers"])
    assert not any(a["op"] == "map_values" for a in s.applied)
    monkeypatch.setattr(agents, "AUTO_CONF", 0.9)
    monkeypatch.setattr(agents, "AUTO_MAX_FRACTION", 0.001)
    s = run_pipeline(D["orders"], D["customers"])
    assert (
        not any(a["op"] == "drop_duplicates" for a in s.applied)
        and s.clean.order_id.duplicated().any()
    )


def test_verifier_rejects_a_fix_that_creates_a_new_problem_or_does_not_help():
    s = State(D["clean"].copy(), D["customers"])
    s.proposals = [
        {
            "op": "map_values",
            "column": "status",
            "mapping": {"completed": "bogus"},
            "confidence": 1.0,
            "issue": ("in_set", "status"),
            "rows": 0,
            "reason": "bad",
        }
    ]
    out = verifier_and_gate(s)
    assert (
        out.applied == []
        and out.queued[0]["decision"] == "rejected"
        and out.clean.equals(D["clean"])
    )


def test_operations_are_allow_listed():
    with pytest.raises(ValueError, match="not allowed"):
        apply_op(D["orders"], {"op": "exec_sql", "sql": "DROP TABLE orders"}, D["customers"])
    with pytest.raises(ValueError, match="only allowed on status"):
        apply_op(
            D["orders"], {"op": "map_values", "column": "amount", "mapping": {}}, D["customers"]
        )


def test_closest_status_matching():
    assert (
        _closest("Completed ")[0] == "completed"
        and _closest("shiped")[0] == "shipped"
        and _closest("canceled")[0] == "cancelled"
    )
    assert _closest("zzzz") == (None, 0.0)


def test_triage_assigns_owner_and_severity():
    t = {(x["check"], x["column"]): x for x in S.triage}
    assert (
        t[("fk_exists", "customer_id")]["severity"] == "high"
        and t[("unique", "order_id")]["category"] == "auto_fixable"
    )
    assert all(x["owner"] for x in S.triage)


def test_checks_handle_empty_frames():
    assert run_checks(D["orders"].iloc[0:0], D["customers"]) == []
    assert isinstance(pd.DataFrame(), pd.DataFrame)


def test_trace_records_each_agent():
    assert [t["agent"] for t in S.trace] == ["detector", "triage", "fixer", "verifier_gate"]
