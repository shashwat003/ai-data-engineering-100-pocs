from dataclasses import replace

import pytest
from long_context_vs_rag_benchmark.bench import (
    Assumptions,
    Setup,
    cost_usd,
    evaluate,
    latency_s,
    reliability,
    scale_projection,
)
from long_context_vs_rag_benchmark.main import run

R = run(24, 30)
A = Assumptions()
S = Setup(12, 20)


def test_long_context_has_perfect_recall_when_everything_fits_and_rag_is_close():
    e = R["curves"]["no degradation"]
    assert e["long_context"]["gold_in_context"] == 1.0
    assert e["rag"]["gold_in_context"] >= 0.9
    assert e["rag"]["context_tokens"] < e["long_context"]["context_tokens"] / 10


def test_rag_is_cheaper_and_faster_by_an_order_of_magnitude_under_stated_prices():
    e = R["curves"]["no degradation"]
    assert e["long_context"]["usd_per_query"] > 10 * e["rag"]["usd_per_query"]
    assert e["long_context"]["latency_s"] > 3 * e["rag"]["latency_s"]


def test_accuracy_conclusion_depends_on_the_assumed_reader_curve():
    flat, steep = R["curves"]["no degradation"], R["curves"]["steep (8%/doubling)"]
    assert (
        flat["long_context"]["est_accuracy"] >= flat["rag"]["est_accuracy"]
    )  # LC wins if the reader does not degrade
    assert (
        steep["long_context"]["est_accuracy"] < steep["rag"]["est_accuracy"]
    )  # RAG wins if it does


def test_truncation_when_corpus_exceeds_window():
    sw = R["small_window"]["long_context"]
    assert sw["gold_in_context"] < 0.5 and sw["context_tokens"] <= 2000


def test_projection_marks_infeasible_sizes_and_cost_ratio_grows():
    p = {r["docs"]: r for r in R["projection"]}
    assert (
        p[100]["fits_window"]
        and not p[100_000]["fits_window"]
        and p[100_000]["long_ctx_usd_per_query"] is None
    )
    assert p[1000]["cost_ratio"] > p[100]["cost_ratio"]


def test_reliability_curve_shape():
    a = replace(A, decay_per_doubling=0.1)
    assert reliability(500, a) == 1.0 and reliability(1000, a) == 1.0
    assert reliability(2000, a) == pytest.approx(0.9) and reliability(10**9, a) == a.floor


def test_cost_and_latency_formulas():
    assert cost_usd(1_000_000, replace(A, answer_tokens=0)) == pytest.approx(A.usd_per_mtok_in)
    assert latency_s(5000, replace(A, answer_tokens=0)) == pytest.approx(1.0)


def test_retrieval_is_timed_and_recall_reported_by_kind():
    r = evaluate(S, A)["rag"]
    assert r["retrieval_ms"] > 0 and set(r["recall_by_kind"]) == {
        "keyword",
        "natural",
        "paraphrase",
        "typo",
    }


def test_deterministic_for_token_and_recall_numbers():
    a, b = evaluate(Setup(12, 20), A), evaluate(Setup(12, 20), A)
    for k in ("long_context", "rag"):
        for f in ("context_tokens", "gold_in_context", "usd_per_query"):
            assert a[k][f] == b[k][f]


def test_projection_does_not_extrapolate_retrieval_quality():
    row = scale_projection(80.0, 200.0, A, sizes=(10,))[0]
    assert "recall" not in row and "accuracy" not in row


def test_assumptions_are_exposed_in_the_results():
    assert R["assumptions"]["usd_per_mtok_in"] == 1.0 and "decode_tok_per_s" in R["assumptions"]
