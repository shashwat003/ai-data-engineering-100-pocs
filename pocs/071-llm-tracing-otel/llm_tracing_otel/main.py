"""POC 071: instrument a simulated RAG app with OpenTelemetry, derive cost/latency metrics, raise alerts."""

from __future__ import annotations

import random
from pathlib import Path

from opentelemetry import context, propagate, trace

from llm_client.base import LLMError, LLMResponse
from pocs_common import write_json
from pocs_common.report import markdown_table

from .aggregate import alerts, summarize
from .tracing import TracedLLM, make_tracer

POC_DIR = Path(__file__).resolve().parent.parent
ROUTES = {
    "faq": ("small-model", 120),
    "search": ("small-model", 400),
    "report": ("large-model", 300),
    "legacy-bloated": ("large-model", 2500),
}


class SimLLM:
    """SIMULATED backend: token counts, latency and failures are drawn from a seeded RNG (no model is called)."""

    def __init__(self, seed: int = 8, fail_rate: float = 0.04):
        self.rng, self.fail_rate = random.Random(seed), fail_rate

    def generate(self, prompt, *, model, **kw):
        if self.rng.random() < self.fail_rate:
            raise LLMError("simulated upstream 503")
        n_in = len(prompt.split())
        lat = (80 if model == "small-model" else 450) * self.rng.uniform(0.7, 1.4) + n_in * 0.4
        return LLMResponse("answer " * 40, model, n_in, self.rng.randint(60, 180), round(lat, 1))


def run(n: int = 400) -> dict:
    tracer, exporter = make_tracer()
    llm = TracedLLM(SimLLM(), tracer)
    rng = random.Random(3)
    for _ in range(n):
        route = rng.choices(list(ROUTES), weights=[4, 3, 2, 1])[0]
        model, ctx_words = ROUTES[route]
        with tracer.start_as_current_span(f"request {route}"):
            with tracer.start_as_current_span("retrieve"):
                pass
            try:
                llm.generate("context " * ctx_words + "question", model=model, route=route)
            except LLMError:
                pass
    spans = exporter.get_finished_spans()
    summary = summarize(spans)
    # trace shape: each LLM span has a request parent in the same trace
    by_id = {s.context.span_id: s for s in spans}
    chat = [s for s in spans if s.name.startswith("chat")]
    parented = sum(
        1 for s in chat if s.parent and by_id[s.parent.span_id].name.startswith("request")
    )
    # context propagation across a service hop via W3C traceparent
    carrier: dict = {}
    with tracer.start_as_current_span("gateway") as gw:
        propagate.inject(carrier)
        gw_trace = gw.get_span_context().trace_id
    tok = context.attach(propagate.extract(carrier))
    try:
        with tracer.start_as_current_span("worker") as w:
            hop_same_trace = w.get_span_context().trace_id == gw_trace
    finally:
        context.detach(tok)
    # privacy: content is absent by default
    leaked = any("app.prompt_redacted" in (s.attributes or {}) for s in spans)
    return {"spans": len(spans), "llm_spans": len(chat), "llm_spans_with_request_parent": parented, "summary": summary,
            "alerts": alerts(summary, budget_usd=0.5), "propagation_same_trace": hop_same_trace, "content_captured_by_default": leaked,
            "trace_api_global_provider_untouched": type(trace.get_tracer_provider()).__name__ != "TracerProvider"}  # fmt: skip


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    rows = [
        [
            k,
            v["calls"],
            f"{v['error_rate']:.1%}",
            f"{v['cost_usd']:.4f}",
            f"{v['cost_per_call']:.5f}",
            f"{v['p50_ms']:.0f}",
            f"{v['p95_ms']:.0f}",
        ]
        for k, v in r["summary"].items()
    ]
    md = ["# Results (measured by `python -m llm_tracing_otel.main`; backend SIMULATED, prices illustrative)", "", f"{r['spans']} spans, {r['llm_spans']} LLM spans, LLM spans with a request parent: {r['llm_spans_with_request_parent']}/{r['llm_spans']}", "",
          markdown_table(["route|model", "calls", "error rate", "cost USD", "cost/call", "p50 ms", "p95 ms"], rows), "", "## Alerts", ""] + [f"- {a}" for a in r["alerts"]] + [
          "", f"- traceparent propagation keeps one trace across a service hop: {r['propagation_same_trace']}", f"- prompt text captured by default: {r['content_captured_by_default']}"]  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
