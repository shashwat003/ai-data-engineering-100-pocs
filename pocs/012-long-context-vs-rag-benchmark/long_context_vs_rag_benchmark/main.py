"""POC 012: long-context vs RAG. Retrieval is measured; cost, latency and reader accuracy are modelled."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .bench import Assumptions, Setup, evaluate, scale_projection

POC_DIR = Path(__file__).resolve().parent.parent


def run(n_systems: int = 24, qpk: int = 40) -> dict:
    base = Assumptions()
    s = Setup(n_systems, qpk)
    # real: RAG gold-document recall as the corpus grows
    growth = []
    for n in (3, 6, 12, 24):
        if n <= n_systems:
            e = evaluate(Setup(n, min(qpk, 3 * n)), base)
            growth.append({"n_docs": e["n_docs"], "rag_recall": e["rag"]["gold_in_context"]})
    curves = {}
    for name, decay in (
        ("no degradation", 0.0),
        ("mild (3%/doubling)", 0.03),
        ("steep (8%/doubling)", 0.08),
    ):
        curves[name] = evaluate(s, replace(base, decay_per_doubling=decay))
    small_window = evaluate(
        s, replace(base, window_tokens=2000)
    )  # what happens when the corpus does not fit
    ref = curves["no degradation"]
    proj = scale_projection(s.total_tokens() / len(s.kb.docs), ref["rag"]["context_tokens"], base)
    return {"assumptions": base.__dict__, "n_docs": len(s.kb.docs), "corpus_tokens": s.total_tokens(), "curves": curves,
            "small_window": small_window, "rag_recall_vs_corpus": growth, "projection": proj}  # fmt: skip


def main() -> None:
    res = run()
    write_json(POC_DIR / "results" / "results.json", res)
    rows = []
    for name, e in res["curves"].items():
        rows.append((name, e["long_context"]["context_tokens"], e["long_context"]["est_accuracy"], round(e["long_context"]["usd_per_query"] * 1000, 2), e["long_context"]["latency_s"],
                     e["rag"]["context_tokens"], e["rag"]["est_accuracy"], round(e["rag"]["usd_per_query"] * 1000, 3), e["rag"]["latency_s"]))  # fmt: skip
    sw = res["small_window"]
    md = (
        f"# Results: long-context vs RAG on a synthetic policy KB ({res['n_docs']} documents, {res['corpus_tokens']} tokens)\n\n"
        "MEASURED: context token counts, whether the gold document reaches the context, retrieval time. "
        "MODELLED (assumptions listed in results.json; illustrative, not vendor prices): cost, latency, and how "
        "reader accuracy falls with context length. No LLM was called, so `est_accuracy` = gold-in-context x an "
        "assumed reliability curve, not a measured answer accuracy.\n\n"
        + markdown_table(["reader degradation assumed", "LC tokens", "LC est. acc", "LC $/1k queries", "LC s", "RAG tokens", "RAG est. acc", "RAG $/1k queries", "RAG s"], rows)
        + f"\n\nRAG retrieval time (measured): {res['curves']['no degradation']['rag']['retrieval_ms']} ms/query; "
        f"RAG gold-document recall@3 by question kind: {res['curves']['no degradation']['rag']['recall_by_kind']}.\n\n"
        f"If the corpus does not fit the window (2,000 tokens here), long-context stuffing truncates: gold document present for "
        f"{sw['long_context']['gold_in_context']:.0%} of questions.\n\n"
        f"RAG recall@3 as the corpus grows (measured): {res['rag_recall_vs_corpus']}.\n\n"
        + markdown_table(["docs", "corpus tokens", "fits 128k window", "long-ctx $/query", "RAG $/1k queries", "cost ratio"],
                         [(p["docs"], p["corpus_tokens"], p["fits_window"], p["long_ctx_usd_per_query"], p["rag_usd_per_query"], p["cost_ratio"]) for p in res["projection"]])
        + "\n\nThe projection extrapolates token counts and cost only; retrieval quality at 10k+ documents is not measured.\n"
    )  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
