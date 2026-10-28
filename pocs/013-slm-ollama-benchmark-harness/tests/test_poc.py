import pandas as pd
from slm_ollama_benchmark_harness.fake_ollama import PROFILES, serve
from slm_ollama_benchmark_harness.harness import memory_mb, pareto, run_benchmark, summarize, wilson
from slm_ollama_benchmark_harness.main import run
from slm_ollama_benchmark_harness.tasks import build_tasks, exact, json_with, max_words

from llm_client import LLMResponse, OllamaClient, StubClient

TASKS = build_tasks()
R = run(repeats=2)
S = {r["model"]: r for r in R["summary"]}


def test_every_task_gold_passes_its_own_checker():
    assert len(TASKS) == 30 and len({t.id for t in TASKS}) == 30
    assert all(t.check(t.gold) for t in TASKS)
    assert not any(t.check("I am not sure.") for t in TASKS)


def test_checkers():
    assert exact("150")("The answer is 150") and not exact("150")("1500")
    assert exact("no")("No.") and not exact("no")("I do not know")
    assert (
        json_with(a=1)('Sure: {"a": 1, "b": 2}')
        and not json_with(a=1)('{"a": 2}')
        and not json_with(a=1)("{bad")
    )
    assert max_words(3, "x")("an x here") and not max_words(3, "x")("too many words about x here")


def test_wilson_interval_known_values():
    lo, hi = wilson(50, 100)
    assert abs(lo - 0.404) < 0.01 and abs(hi - 0.596) < 0.01
    assert wilson(0, 0) == (0.0, 0.0)
    lo, hi = wilson(10, 10)
    assert hi == 1.0 and lo > 0.69


def test_quality_ranking_matches_scripted_profiles_and_is_reproducible():
    order = [r["model"] for r in R["summary"]]
    assert order[0] == "fake-mid:7b" and order[-1] == "fake-tiny:1b"
    assert (
        S["fake-mid:7b"]["pass_rate"]
        > S["fake-small:3b"]["pass_rate"]
        > S["fake-tiny:1b"]["pass_rate"]
    )
    assert {m: S[m]["pass_rate"] for m in S} == {
        r["model"]: r["pass_rate"] for r in run(repeats=2)["summary"]
    }


def test_latency_and_memory_are_measured_and_ordered():
    assert S["fake-tiny:1b"]["p50_ms"] < S["fake-small:3b"]["p50_ms"] < S["fake-mid:7b"]["p50_ms"]
    assert S["fake-small:3b"]["memory_mb"] == PROFILES["fake-small:3b"]["mb"]
    assert all(r["p95_ms"] >= r["p50_ms"] for r in R["summary"])


def test_dominated_model_is_not_pareto_optimal():
    assert (
        not S["fake-wasteful:7b"]["pareto"]
        and S["fake-mid:7b"]["pareto"]
        and S["fake-tiny:1b"]["pareto"]
    )


def test_missing_model_is_skipped_not_failed():
    assert R["skipped"] == {"llama3.2:3b": "not installed"}
    assert "llama3.2:3b" not in S


def test_unreachable_server_skips_everything_without_crashing():
    rep = run_benchmark(OllamaClient("http://127.0.0.1:1", timeout=1), ["a", "b"], TASKS[:2])
    assert set(rep.skipped) == {"a", "b"} and rep.samples == [] and summarize(rep).empty


def test_server_errors_are_recorded_as_failures_with_error_class():
    srv, url = serve({t.prompt: t.gold for t in TASKS}, fail_prefix="What is")
    try:
        rep = run_benchmark(OllamaClient(url, timeout=5), ["fake-mid:7b"], TASKS, repeats=1)
    finally:
        srv.shutdown()
    df = summarize(rep)
    assert df.iloc[0].errors == sum(t.prompt.startswith("What is") for t in TASKS) == 7
    bad = [s for s in rep.samples if s.error]
    assert all(not s.ok and s.error == "LLMError" for s in bad)


def test_unstable_outputs_are_counted():
    calls = {"n": 0}

    def respond(prompt):
        calls["n"] += 1
        return TASKS[0].gold if calls["n"] % 2 else "different"

    class C(StubClient):
        def list_models(self):
            return [{"name": "m:1b"}]

        def _request(self, path):
            return {"models": []}

    rep = run_benchmark(C(respond), ["m:1b"], TASKS[:1], repeats=4, warmup=0)
    assert summarize(rep).iloc[0].unstable_tasks == 1
    assert rep.memory_mb["m:1b"] is None  # never invented


def test_memory_none_when_endpoint_missing():
    class NoPs:
        def _request(self, path):
            raise __import__("llm_client").LLMError("404")

    assert memory_mb(NoPs(), "x") is None


def test_pareto_handles_unknown_memory():
    df = pd.DataFrame([
        {"pass_rate": 0.9, "p50_ms": 10.0, "memory_mb": None},
        {"pass_rate": 0.8, "p50_ms": 20.0, "memory_mb": None},
    ])  # fmt: skip
    assert pareto(df) == [True, False]


def test_stub_generate_signature_compat():
    r = StubClient("x").generate("p", model="m", temperature=0.0, max_tokens=4, seed=1)
    assert isinstance(r, LLMResponse)
