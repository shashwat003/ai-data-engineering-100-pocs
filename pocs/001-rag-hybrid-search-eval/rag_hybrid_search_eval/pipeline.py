"""Retrieval experiment: build retrievers, score them, run ablations, render a report."""

from __future__ import annotations

import platform
import re

import numpy as np

from data_gen import KnowledgeBase, QAItem
from eval_kit import bootstrap_ci, contains_answer, evaluate_run, support_score
from pocs_common import markdown_table
from retrieval_kit import (
    BM25Retriever,
    CachedEmbedder,
    DenseRetriever,
    HybridRetriever,
    LSAEmbedder,
    Retriever,
    tokenize,
)

KINDS = ("keyword", "natural", "paraphrase", "typo")
K_CONTEXT = 3  # documents handed to the answer step
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def build_retrievers(
    kb: KnowledgeBase, *, dims: int = 64, alpha: float = 0.5, neural=None
) -> dict[str, Retriever]:
    """Baselines (BM25, LSA, LSA hybrids) always; neural dense + hybrids when an embedder is given."""
    ids = [d.doc_id for d in kb.docs]
    texts = [d.indexed_text for d in kb.docs]
    bm25 = BM25Retriever(ids, texts)
    lsa = DenseRetriever(ids, texts, LSAEmbedder(dims=dims), name="dense_lsa")
    out: dict[str, Retriever] = {
        "bm25": bm25,
        "dense_lsa": lsa,
        "hybrid_lsa_rrf": HybridRetriever(bm25, lsa, mode="rrf", name="hybrid_lsa_rrf"),
        "hybrid_lsa_weighted": HybridRetriever(
            bm25, lsa, mode="weighted", alpha=alpha, name="hybrid_lsa_weighted"
        ),
    }
    if neural is not None:
        dense = DenseRetriever(ids, texts, neural, name="dense_neural")
        out["dense_neural"] = dense
        out["hybrid_neural_rrf"] = HybridRetriever(
            bm25, dense, mode="rrf", name="hybrid_neural_rrf"
        )
        out["hybrid_neural_weighted"] = HybridRetriever(
            bm25, dense, mode="weighted", alpha=alpha, name="hybrid_neural_weighted"
        )
    return out


def run_queries(retriever: Retriever, qa: tuple[QAItem, ...], k: int = 10) -> dict[str, list[str]]:
    return {q.qid: [d for d, _ in retriever.search(q.question, k)] for q in qa}


def extractive_answer(question: str, contexts: list[str]) -> str:
    """Trivial answer step: the context sentence sharing the most words with the question.

    This is a deterministic stand-in for an LLM generator so that end-to-end numbers do not depend
    on a model download. It measures how useful the retrieved context is, not LLM quality.
    """
    q_tokens = set(tokenize(question))
    best, best_score = "", -1
    for sentence in (s for ctx in contexts for s in _SENTENCE.split(ctx)):
        score = len(q_tokens & set(tokenize(sentence)))
        if score > best_score:
            best, best_score = sentence, score
    return best


def _answer_metrics(
    retriever: Retriever, kb: KnowledgeBase, qa: tuple[QAItem, ...]
) -> dict[str, float]:
    text_of = {d.doc_id: d.text for d in kb.docs}
    has_answer = correct = grounded = 0.0
    for q in qa:
        contexts = [text_of[d] for d, _ in retriever.search(q.question, K_CONTEXT)]
        answer = extractive_answer(q.question, contexts)
        has_answer += any(contains_answer(c, q.gold_answer) for c in contexts)
        correct += contains_answer(answer, q.gold_answer)
        grounded += support_score(answer, contexts)
    n = len(qa)
    return {
        f"context_has_answer@{K_CONTEXT}": has_answer / n,
        f"extractive_accuracy@{K_CONTEXT}": correct / n,
        "groundedness": grounded / n,
    }


def evaluate_retriever(retriever: Retriever, kb: KnowledgeBase) -> dict:
    qrels = {q.qid: {q.gold_doc_id} for q in kb.qa}
    run = run_queries(retriever, kb.qa)
    overall = evaluate_run(run, qrels)
    by_kind = {}
    for kind in KINDS:
        subset = {q.qid: qrels[q.qid] for q in kb.qa if q.kind == kind}
        by_kind[kind] = evaluate_run({k: run[k] for k in subset}, subset).aggregate
    rr = [m["mrr@10"] for m in overall.per_query.values()]
    return {
        "overall": overall.aggregate,
        "mrr_ci95": list(bootstrap_ci(rr, seed=0)),
        "by_kind": by_kind,
        "answer": _answer_metrics(retriever, kb, kb.qa),
        "_rr": rr,
    }


def _mrr(retriever: Retriever, kb: KnowledgeBase) -> float:
    qrels = {q.qid: {q.gold_doc_id} for q in kb.qa}
    return evaluate_run(run_queries(retriever, kb.qa), qrels).aggregate["mrr@10"]


def _alpha_sweep(sparse: Retriever, dense: Retriever, kb: KnowledgeBase, alphas) -> list[dict]:
    rows = []
    for alpha in alphas:  # alpha = weight on dense; 0.0 equals BM25, 1.0 equals dense
        r = HybridRetriever(sparse, dense, mode="weighted", alpha=alpha)
        rows.append({"alpha": alpha, "mrr@10": _mrr(r, kb)})
    return rows


COMPARISONS = (  # (a, b): paired difference in reciprocal rank, a minus b
    ("hybrid_lsa_rrf", "bm25"),
    ("hybrid_lsa_rrf", "dense_lsa"),
    ("dense_neural", "bm25"),
    ("dense_neural", "dense_lsa"),
    ("hybrid_neural_rrf", "bm25"),
    ("hybrid_neural_rrf", "dense_neural"),
    ("hybrid_neural_rrf", "hybrid_lsa_rrf"),
)


def run_experiment(
    kb: KnowledgeBase,
    *,
    neural=None,
    dims: int = 64,
    alphas: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0),
    dims_grid: tuple[int, ...] = (8, 16, 32, 64, 96),
) -> dict:
    """Evaluate baselines, and neural retrievers when `neural` (an Embedder) is supplied."""
    neural = CachedEmbedder(neural) if neural is not None else None
    retrievers = build_retrievers(kb, dims=dims, neural=neural)
    evaluated = {name: evaluate_retriever(r, kb) for name, r in retrievers.items()}
    rr = {name: ev.pop("_rr") for name, ev in evaluated.items()}
    comparisons = []
    for a, b in COMPARISONS:
        if a in rr and b in rr:
            diffs = [x - y for x, y in zip(rr[a], rr[b], strict=True)]
            lo, hi = bootstrap_ci(diffs, seed=0)
            comparisons.append(
                {"a": a, "b": b, "mean_rr_diff": sum(diffs) / len(diffs), "ci95": [lo, hi]}
            )
    results = {
        "meta": {
            "kb_fingerprint": kb.fingerprint(),
            "n_docs": len(kb.docs),
            "n_queries": len(kb.qa),
            "queries_per_kind": len(kb.qa) // len(KINDS),
            "lsa_dims": dims,
            "neural": neural.describe() if neural is not None else None,
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "retrievers": evaluated,
        "comparisons": comparisons,
        "ablation_alpha": _alpha_sweep(retrievers["bm25"], retrievers["dense_lsa"], kb, alphas),
        "ablation_alpha_neural": (
            _alpha_sweep(retrievers["bm25"], retrievers["dense_neural"], kb, alphas)
            if neural is not None
            else []
        ),
        "ablation_dims": [],
    }
    for d in dims_grid:
        lsa = build_retrievers(kb, dims=d)
        results["ablation_dims"].append(
            {
                "dims": d,
                "dense_lsa_mrr@10": _mrr(lsa["dense_lsa"], kb),
                "hybrid_lsa_rrf_mrr@10": _mrr(lsa["hybrid_lsa_rrf"], kb),
            }
        )
    return results


def render_markdown(results: dict) -> str:
    meta, rets = results["meta"], results["retrievers"]
    neural = meta["neural"]
    title = (
        "# Results: neural comparison (measured locally; generated by `--neural`, do not edit)"
        if neural
        else "# Results (generated by `make demo`; do not edit)"
    )
    lines = [
        title,
        "",
        f"- {meta['n_docs']} documents, {meta['n_queries']} questions "
        f"({meta['queries_per_kind']} per kind); LSA dims={meta['lsa_dims']}",
        f"- dataset fingerprint `{meta['kb_fingerprint'][:16]}`, python {meta['python']}, "
        f"numpy {meta['numpy']}",
    ]
    if neural:
        lines.append("- neural embedder: " + ", ".join(f"{k}=`{v}`" for k, v in neural.items()))
    lines += [
        "- Synthetic data; absolute numbers say nothing about real corpora. Compare retrievers "
        "against each other only.",
        "",
        "## Retrieval quality (all question kinds)",
        "",
    ]
    cols = ["recall@1", "recall@3", "recall@5", "mrr@10", "ndcg@10"]
    rows = [
        [n, *[r["overall"][c] for c in cols], f"{r['mrr_ci95'][0]:.3f}-{r['mrr_ci95'][1]:.3f}"]
        for n, r in rets.items()
    ]
    lines += [
        markdown_table(["retriever", *cols, "mrr 95% CI"], rows),
        "",
        "## MRR@10 by question kind",
        "",
        markdown_table(
            ["retriever", *KINDS],
            [[n, *[r["by_kind"][k]["mrr@10"] for k in KINDS]] for n, r in rets.items()],
        ),
        "",
        "## Paired comparisons (difference in reciprocal rank, a minus b)",
        "",
        "A difference is only credible when its 95% paired-bootstrap interval excludes 0.",
        "",
        markdown_table(
            ["a", "b", "mean diff", "95% CI", "excludes 0?"],
            [
                [
                    c["a"],
                    c["b"],
                    c["mean_rr_diff"],
                    f"{c['ci95'][0]:+.3f} to {c['ci95'][1]:+.3f}",
                    "yes" if c["ci95"][0] > 0 or c["ci95"][1] < 0 else "no",
                ]
                for c in results["comparisons"]
            ],
        ),
        "",
        f"## End-to-end (top-{K_CONTEXT} contexts, extractive answer step)",
        "",
    ]
    a_cols = list(next(iter(rets.values()))["answer"])
    lines += [
        markdown_table(
            ["retriever", *a_cols],
            [[n, *[r["answer"][c] for c in a_cols]] for n, r in rets.items()],
        ),
        "",
        "`groundedness` is 1.0 by construction for an extractive answer; it becomes informative "
        "when an LLM generator is plugged in.",
        "",
        "## Ablation: weighted fusion, alpha = weight on dense (0 = BM25 only, 1 = dense only)",
        "",
        markdown_table(
            ["alpha", "LSA mrr@10", *(["neural mrr@10"] if neural else [])],
            [
                [
                    a["alpha"],
                    a["mrr@10"],
                    *([results["ablation_alpha_neural"][i]["mrr@10"]] if neural else []),
                ]
                for i, a in enumerate(results["ablation_alpha"])
            ],
        ),
        "",
        "## Ablation: LSA dimensions",
        "",
        markdown_table(
            ["dims", "dense_lsa mrr@10", "hybrid_lsa_rrf mrr@10"],
            [
                [d["dims"], d["dense_lsa_mrr@10"], d["hybrid_lsa_rrf_mrr@10"]]
                for d in results["ablation_dims"]
            ],
        ),
    ]
    return "\n".join(lines) + "\n"
