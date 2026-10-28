import pytest
from llm_tracing_otel.aggregate import alerts, summarize
from llm_tracing_otel.main import run
from llm_tracing_otel.tracing import TracedLLM, cost_usd, make_tracer, redact

from llm_client.base import LLMError, LLMResponse


class Fixed:
    def __init__(self, fail=False):
        self.fail = fail

    def generate(self, prompt, *, model, **kw):
        if self.fail:
            raise LLMError("boom")
        return LLMResponse("hello alice@example.com 123456789", model, 1000, 500, 42.0)


def test_cost_arithmetic_and_unknown_model():
    assert cost_usd("small-model", 1000, 1000) == pytest.approx(0.002)
    assert cost_usd("large-model", 2000, 0) == pytest.approx(0.01)
    with pytest.raises(KeyError):
        cost_usd("mystery", 1, 1)


def test_span_attributes_and_no_content_by_default():
    tracer, ex = make_tracer()
    TracedLLM(Fixed(), tracer).generate(
        "secret prompt bob@example.com", model="small-model", route="faq"
    )
    (s,) = ex.get_finished_spans()
    a = s.attributes
    assert (
        a["gen_ai.usage.input_tokens"] == 1000
        and a["llm.cost_usd"] == pytest.approx(0.00125)
        and a["app.route"] == "faq"
    )
    assert not any("redacted" in k for k in a) and "secret" not in str(a)


def test_opt_in_capture_is_redacted():
    tracer, ex = make_tracer()
    TracedLLM(Fixed(), tracer, capture_content=True).generate(
        "mail bob@example.com id 987654321", model="small-model"
    )
    a = ex.get_finished_spans()[0].attributes
    assert (
        "bob@example.com" not in a["app.prompt_redacted"]
        and "[EMAIL]" in a["app.prompt_redacted"]
        and "[NUM]" in a["app.prompt_redacted"]
    )
    assert "alice@example.com" not in a["app.response_redacted"]
    assert len(redact("x" * 1000)) == 200


def test_error_is_recorded_and_reraised():
    tracer, ex = make_tracer()
    with pytest.raises(LLMError):
        TracedLLM(Fixed(fail=True), tracer).generate("p", model="small-model")
    (s,) = ex.get_finished_spans()
    assert s.status.status_code.name == "ERROR" and any(e.name == "exception" for e in s.events)


def test_unknown_model_price_failure_does_not_hide_response_path():
    tracer, ex = make_tracer()
    with pytest.raises(KeyError):
        TracedLLM(Fixed(), tracer).generate("p", model="mystery")


def test_summary_and_alerts():
    tracer, ex = make_tracer()
    llm = TracedLLM(Fixed(), tracer)
    for _ in range(4):
        llm.generate("p", model="small-model", route="a")
    for r in ("b", "c"):
        llm.generate("p", model="small-model", route=r)
    llm.generate("p", model="large-model", route="d")
    s = summarize(ex.get_finished_spans())
    assert s["a|small-model"]["calls"] == 4 and s["a|small-model"]["error_rate"] == 0
    al = alerts(s, budget_usd=0.001)
    assert any("budget" in x for x in al) and any(
        "d|large-model" in x and "median" in x for x in al
    )
    assert alerts({}, budget_usd=1) == []


def test_demo_end_to_end_is_deterministic():
    r1, r2 = run(150), run(150)
    assert r1["summary"] == r2["summary"]
    assert (
        r1["llm_spans_with_request_parent"] == r1["llm_spans"]
        and r1["propagation_same_trace"]
        and not r1["content_captured_by_default"]
    )
    assert any("legacy-bloated" in a for a in r1["alerts"])
