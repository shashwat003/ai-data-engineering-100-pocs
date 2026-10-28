"""OpenTelemetry tracing and cost telemetry around an LLM client.

Spans follow the (experimental) GenAI semantic-convention attribute names where they exist
(`gen_ai.request.model`, `gen_ai.usage.input_tokens`, ...); cost is a custom `llm.cost_usd` attribute.
Prompt/response text is NOT recorded by default; opt-in capture is redacted (emails, long digit runs).
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Status, StatusCode

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
DIGITS = re.compile(r"\d{6,}")
PRICES = {
    "small-model": (0.0005, 0.0015),
    "large-model": (0.005, 0.015),
}  # USD per 1k tokens (in, out): illustrative, not a real price list


def redact(text: str, limit: int = 200) -> str:
    return DIGITS.sub("[NUM]", EMAIL.sub("[EMAIL]", text))[:limit]


def cost_usd(model: str, in_tokens: int, out_tokens: int, prices: Mapping = PRICES) -> float:
    if model not in prices:
        raise KeyError(f"no price for model {model!r}")
    pin, pout = prices[model]
    return round(in_tokens / 1000 * pin + out_tokens / 1000 * pout, 6)


def make_tracer() -> tuple[trace.Tracer, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()  # a private provider: never touches the global one
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("llm_tracing_otel"), exporter


class TracedLLM:
    def __init__(self, client, tracer, *, capture_content: bool = False, prices: Mapping = PRICES):
        self.client, self.tracer, self.capture, self.prices = (
            client,
            tracer,
            capture_content,
            prices,
        )

    def generate(self, prompt: str, *, model: str, route: str = "default", **kw):
        with self.tracer.start_as_current_span(f"chat {model}") as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.request.model", model)
            span.set_attribute("app.route", route)
            if self.capture:
                span.set_attribute("app.prompt_redacted", redact(prompt))
            try:
                r = self.client.generate(prompt, model=model, **kw)
            except Exception as exc:  # recorded then re-raised: tracing must not swallow failures
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise
            span.set_attribute("gen_ai.usage.input_tokens", r.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", r.output_tokens)
            span.set_attribute("llm.latency_ms", r.latency_ms)
            span.set_attribute(
                "llm.cost_usd", cost_usd(model, r.input_tokens, r.output_tokens, self.prices)
            )
            if self.capture:
                span.set_attribute("app.response_redacted", redact(r.text))
            return r
