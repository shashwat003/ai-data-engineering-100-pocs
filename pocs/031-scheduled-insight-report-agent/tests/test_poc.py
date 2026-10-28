import hashlib
from datetime import date, datetime, timedelta

import pytest
import scheduled_insight_report_agent.facts as F
from scheduled_insight_report_agent.cron import Cron
from scheduled_insight_report_agent.main import FlakySink, run
from scheduled_insight_report_agent.metrics import INCIDENT_DAY, daily
from scheduled_insight_report_agent.narrative import SimWriter, render
from scheduled_insight_report_agent.scheduler import (
    MAX_ATTEMPTS,
    MAX_CATCHUP,
    FileOutbox,
    Ledger,
    build_report,
    run_due,
)
from scheduled_insight_report_agent.verify import check, parse_number

ROWS = daily()
D0 = date(2025, 1, 1)
R = run()


def test_cron_matching_and_catch_up_ticks():
    c = Cron("0 8 * * 1")
    assert (
        c.matches(datetime(2025, 4, 7, 8, 0))
        and not c.matches(datetime(2025, 4, 8, 8, 0))
        and not c.matches(datetime(2025, 4, 7, 8, 1))
    )
    assert [t.day for t in c.ticks(datetime(2025, 4, 1), datetime(2025, 4, 30))] == [7, 14, 21, 28]
    assert (
        Cron("*/15 9-10 * * *")
        .ticks(datetime(2025, 1, 1, 8, 59), datetime(2025, 1, 1, 10, 59))
        .__len__()
        == 8
    )
    assert Cron("0 0 1,15 * 3").matches(datetime(2025, 1, 15)) and Cron("0 0 1,15 * 3").matches(
        datetime(2025, 1, 8)
    )  # dom OR dow when both set


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "* * * *",
        "61 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "*/0 * * * *",
        "a b c d e",
        "* * * 13 *",
    ],
)
def test_invalid_cron_is_rejected(bad):
    with pytest.raises(ValueError):
        Cron(bad)


def test_facts_match_a_direct_computation():
    f = F.build_facts(ROWS, D0 + timedelta(days=60))
    cur = sum(
        r["revenue"]
        for r in ROWS
        if D0 + timedelta(days=54) <= date.fromisoformat(r["date"]) <= D0 + timedelta(days=60)
    )
    assert abs(f["revenue"] - cur) < 1 and f["days"] == 7


def test_insufficient_history_is_an_error():
    with pytest.raises(ValueError, match="history"):
        F.build_facts(ROWS, D0 + timedelta(days=10))


def test_incident_is_detected_with_few_false_alarms():
    inc = {INCIDENT_DAY, INCIDENT_DAY + 1, INCIDENT_DAY + 2}
    flagged = set()
    for end in range(49, 120):
        flagged |= {
            (date.fromisoformat(d) - D0).days
            for d, _ in F.build_facts(ROWS, D0 + timedelta(days=end))["anomalies"]
        }
    assert len(flagged & inc) >= 2 and len(flagged - inc) <= 3  # 70 days scanned
    assert R["facts"]["anomalies"] and R["facts"]["revenue_change_pct"] < 0


def test_template_report_is_grounded_and_link_free():
    assert R["template_check"] == {"ok": True, "ungrounded_numbers": [], "links": []}
    assert "Revenue was $" in R["template"]


def test_faithful_writer_is_accepted_hallucinating_and_linking_writers_fall_back():
    w = R["writers"]
    assert w["faithful"]["source"] == "writer" and w["faithful"]["ok"]
    assert w["hallucinating"]["source"] == "template_fallback" and {"14%", "61"} <= set(
        w["hallucinating"]["ungrounded"]
    )
    assert w["linking"]["source"] == "template_fallback" and w["linking"]["links"] >= 2


def test_verifier_number_handling():
    f = F.build_facts(ROWS, D0 + timedelta(days=60))
    assert check(f"Revenue was ${f['revenue']:,}.", f)["ok"]
    assert not check(f"Revenue was ${f['revenue'] + 5000:,}.", f)["ok"]
    assert check("On 2025-04-13 nothing odd.", f)["ok"]  # dates are not numbers to ground
    assert (
        parse_number("$1.5M") == 1_500_000
        and parse_number("-12%") == 12
        and parse_number("3,400") == 3400
    )


def test_unknown_writer_mode_errors():
    with pytest.raises(ValueError):
        SimWriter("magic").rewrite("x", {})


def test_template_is_sandboxed_and_strict():
    with pytest.raises((KeyError, TypeError, __import__("jinja2").UndefinedError)):
        render({"period": "p"})  # missing keys must fail loudly, not print blanks


def test_catch_up_runs_every_missed_tick_and_retries_a_failed_send():
    assert [(r["period"], r["action"]) for r in R["first_run"]] == [
        ("2025-04-06", "send_failed"),
        ("2025-04-06", "delivered"),
        ("2025-04-13", "delivered"),
        ("2025-04-20", "delivered"),
        ("2025-04-27", "delivered"),
    ]
    assert R["first_run"][1]["attempts"] == 2


def test_restart_does_not_resend_and_outbox_has_distinct_keys():
    assert {r["action"] for r in R["second_run"]} == {"already_delivered"}
    assert R["files_in_outbox"] == 4 == R["distinct_keys"]


def test_permanent_sink_failure_stops_after_max_attempts_and_is_recorded(tmp_path):
    ledger = Ledger()
    sink = FlakySink(FileOutbox(tmp_path), fail_first=99)
    out = run_due(
        "r",
        Cron("0 8 * * 1"),
        datetime(2025, 4, 7, 9, 0),
        datetime(2025, 4, 6, 9, 0),
        ROWS,
        sink,
        ledger,
    )
    assert (
        sink.calls == MAX_ATTEMPTS and [o["action"] for o in out] == ["send_failed"] * MAX_ATTEMPTS
    )
    assert ledger.get("r", "2025-04-06") == ("failed", MAX_ATTEMPTS) and ledger.delivered("r") == []


def test_catch_up_is_capped(tmp_path):
    out = run_due(
        "r",
        Cron("0 8 * * *"),
        datetime(2025, 4, 28, 9),
        datetime(2025, 3, 1),
        ROWS,
        FileOutbox(tmp_path),
        Ledger(),
    )
    assert len(out) == MAX_CATCHUP


def test_idempotency_key_is_stable_per_report_and_period(tmp_path):
    box = FileOutbox(tmp_path)
    for _ in range(2):
        run_due(
            "weekly",
            Cron("0 8 * * 1"),
            datetime(2025, 4, 7, 9),
            datetime(2025, 4, 6, 9),
            ROWS,
            box,
            Ledger(),
        )  # fresh ledger: sink still dedupes
    assert box.sent() == [hashlib.sha256(b"weekly:2025-04-06").hexdigest()[:16]]


def test_build_report_without_writer_uses_template():
    text, source, chk = build_report(ROWS, D0 + timedelta(days=60))
    assert source == "template" and chk["ok"]
