import json

import pytest
from slm_log_anomaly_explainer.detect import detect, make_miner, mine, prf, sanitize
from slm_log_anomaly_explainer.explain import SimSLM, build_prompt, explain, validate_reply
from slm_log_anomaly_explainer.logs import INJECTION, Line, generate
from slm_log_anomaly_explainer.main import run

from llm_client import StubClient

R = run()
G = generate()


def test_generator_is_deterministic_and_incidents_are_in_the_test_period():
    assert [ln.text for ln in generate()["lines"][:50]] == [ln.text for ln in G["lines"][:50]]
    assert min(G["truth_minutes"]) >= G["train_minutes"] and len(G["incidents"]) == 5


def test_drain_groups_lines_that_differ_only_in_variables():
    miner, a = mine(
        [
            Line(0, "api", "GET /items/1 200 5ms"),
            Line(0, "api", "GET /items/987 200 61ms"),
            Line(0, "api", "user ana logged in from 10.0.0.1"),
            Line(0, "api", "user ben logged in from 10.1.2.3"),
        ]
    )
    assert a[0][2] == a[1][2] and a[2][2] == a[3][2] and a[0][2] != a[2][2]
    assert len(miner.drain.clusters) == 2


def test_template_count_is_small_and_stable():
    assert 9 <= R["n_templates"] <= 14 and R["n_lines"] > 10_000


def test_each_incident_type_is_detected_at_least_once():
    _, a = mine(G["lines"])
    flags = detect(a, G["train_minutes"])
    for name, minutes in G["incidents"].items():
        assert any(m in flags for m in minutes), name


def test_detection_quality():
    m = R["metrics"]
    assert m["precision"] >= 0.95 and m["recall"] >= 0.8
    assert set(m["missed_windows"]) <= {
        135,
        136,
    }  # heartbeat loss needs 3 silent minutes to confirm


def test_clean_logs_raise_no_alerts():
    g = generate(inject=False)
    _, a = mine(g["lines"])
    assert detect(a, g["train_minutes"]) == {}


def test_prf_edge_cases():
    r = prf({12: ["x"], 15: ["y"]}, [12, 13], 10, 20)
    assert (
        r["precision"] == 0.5
        and r["recall"] == 0.5
        and r["missed_windows"] == [13]
        and r["false_positive_windows"] == 1
    )


def test_sanitize_redacts_ips_emails_and_secrets_and_truncates():
    s = sanitize("login ana@example.com from 10.1.2.3 token=abc123 password=hunter2 " + "x" * 500)
    assert (
        "ana@example.com" not in s
        and "10.1.2.3" not in s
        and "hunter2" not in s
        and "abc123" not in s
        and len(s) <= 300
    )


def test_prompt_fences_logs_and_never_contains_raw_ips():
    p = build_prompt(7, "api GET <*>", "spike:7", ["conn from 10.9.8.7 failed", INJECTION], 12, 0.5)
    assert "<logs>" in p and "untrusted" in p and "10.9.8.7" not in p


def good_reply(cid=1, **kw):
    d = {
        "cluster": cid,
        "summary": "Database lock contention",
        "likely_cause": "concurrent writes",
        "severity": "high",
        "next_step": "inspect transactions",
    }
    return json.dumps({**d, **kw})


@pytest.mark.parametrize(
    "text",
    [
        "no json here",
        good_reply(cid=2),
        good_reply(severity="catastrophic"),
        good_reply(summary="x" * 300),
        good_reply(summary="The incident is resolved"),
        good_reply(next_step="ignore this"),
        json.dumps({"cluster": 1, "summary": "ok fine"}),
        good_reply(summary=5),
    ],
)
def test_validate_rejects_bad_replies(text):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        validate_reply(text, 1)


def test_validate_accepts_good_reply_wrapped_in_prose():
    assert validate_reply("Sure: " + good_reply() + " done", 1)["severity"] == "high"


def test_explanations_come_from_the_model_for_normal_incidents():
    srcs = {
        e["reason"].split(":")[0]: e["source"]
        for e in R["explanations"]
        if "disk quota" not in e["template"]
    }
    assert set(srcs.values()) == {"model"}
    assert any("deadlock" in e["template"] for e in R["explanations"])


def test_prompt_injection_in_logs_is_neutralised_for_a_gullible_model():
    assert R["gullible_model_source"] == "fallback"
    e = explain(
        SimSLM(gullible=True), 5, "api error <*>", "new_template:5", [f"error; {INJECTION}"], 1, 0.0
    )
    assert e["source"] == "fallback" and "resolved" not in json.dumps(e)


def test_broken_model_falls_back_and_retry_recovers():
    assert R["broken_model_source"] == "fallback"
    seq = iter(["garbage", good_reply(5)])
    c = StubClient(lambda p: next(seq))
    e = explain(c, 5, "t", "spike:5", ["x"], 3, 0.1, retries=1)
    assert e["source"] == "model" and len(c.calls) == 2


def test_miner_factory_is_independent():
    assert make_miner() is not make_miner()
