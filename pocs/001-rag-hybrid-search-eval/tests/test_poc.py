import json

import pytest
from rag_hybrid_search_eval.main import main
from rag_hybrid_search_eval.pipeline import (
    build_retrievers,
    extractive_answer,
    render_markdown,
    run_experiment,
)

from data_gen import generate_knowledge_base


@pytest.fixture(scope="module")
def kb():
    return generate_knowledge_base(n_systems=8, queries_per_kind=15, seed=5)


@pytest.fixture(scope="module")
def results(kb):
    return run_experiment(kb, dims=32, dims_grid=(8, 32))


def test_all_retrievers_reported_with_valid_metrics(results):
    assert set(results["retrievers"]) == {
        "bm25",
        "dense_lsa",
        "hybrid_lsa_rrf",
        "hybrid_lsa_weighted",
    }
    for r in results["retrievers"].values():
        assert all(0.0 <= v <= 1.0 for v in r["overall"].values())
        lo, hi = r["mrr_ci95"]
        assert lo <= r["overall"]["mrr@10"] <= hi
        assert set(r["by_kind"]) == {"keyword", "natural", "paraphrase", "typo"}


def test_bm25_solves_runbook_code_lookups(results):
    # Property of the data: a unique runbook code is an exact-match token.
    assert results["retrievers"]["bm25"]["by_kind"]["keyword"]["recall@1"] >= 0.95


def test_alpha_endpoints_match_single_retrievers(results):
    by_alpha = {a["alpha"]: a["mrr@10"] for a in results["ablation_alpha"]}
    assert by_alpha[0.0] == pytest.approx(results["retrievers"]["bm25"]["overall"]["mrr@10"])
    assert by_alpha[1.0] == pytest.approx(results["retrievers"]["dense_lsa"]["overall"]["mrr@10"])


def test_experiment_is_deterministic(kb, results):
    again = run_experiment(kb, dims=32, dims_grid=(8, 32))
    assert again == results


def test_extractive_answer_picks_overlapping_sentence():
    ctx = ["Alpha is red. Beta backups are retained for 30 days. Gamma is blue."]
    assert extractive_answer("How long are Beta backups retained?", ctx).startswith("Beta backups")


def test_retriever_returns_gold_document_for_exact_title(kb):
    doc = kb.docs[0]
    top = build_retrievers(kb, dims=16)["bm25"].search(doc.title, 3)
    assert doc.doc_id in [d for d, _ in top]


def test_report_and_cli_outputs(results, tmp_path):
    md = render_markdown(results)
    assert "## Retrieval quality" in md and "hybrid_lsa_rrf" in md and "Ablation" in md
    main(["--n-systems", "6", "--queries-per-kind", "5", "--out-dir", str(tmp_path)])
    assert json.loads((tmp_path / "results.json").read_text())["meta"]["n_docs"] == 30
    assert (tmp_path / "results.md").read_text().startswith("# Results")


# ---- optional neural path, exercised with a deterministic fake embedder (NOT a real model)

import numpy as np  # noqa: E402
from rag_hybrid_search_eval import main as main_mod  # noqa: E402

from retrieval_kit import tokenize  # noqa: E402


class FakeNeural:
    """Hashing bag-of-words embedder standing in for a neural model in tests only."""

    def fit(self, texts):
        return None

    def encode(self, texts):
        out = np.zeros((len(texts), 64))
        for i, t in enumerate(texts):
            for tok in tokenize(t):
                out[i, sum(map(ord, tok)) % 64] += 1.0
        n = np.linalg.norm(out, axis=1, keepdims=True)
        return np.divide(out, n, out=np.zeros_like(out), where=n > 0)

    def describe(self):
        return {"kind": "fake", "model": "hashing-bow"}


def test_neural_rows_added_and_baselines_unchanged(kb, results):
    with_neural = run_experiment(kb, neural=FakeNeural(), dims=32, dims_grid=(8, 32))
    names = set(with_neural["retrievers"])
    assert {"dense_neural", "hybrid_neural_rrf", "hybrid_neural_weighted"} <= names
    for base in results["retrievers"]:  # adding a neural model must not perturb the baselines
        assert with_neural["retrievers"][base] == results["retrievers"][base]
    assert with_neural["meta"]["neural"] == {"kind": "fake", "model": "hashing-bow"}
    assert {(c["a"], c["b"]) for c in with_neural["comparisons"]} > {("dense_neural", "bm25")}
    assert len(with_neural["ablation_alpha_neural"]) == len(with_neural["ablation_alpha"])
    md = render_markdown(with_neural)
    assert "neural comparison" in md and "Paired comparisons" in md and "hashing-bow" in md


def test_comparison_ci_brackets_mean(results):
    for c in results["comparisons"]:
        assert c["ci95"][0] <= c["mean_rr_diff"] <= c["ci95"][1]


def test_cli_neural_writes_separate_files(tmp_path, monkeypatch):
    monkeypatch.setattr(main_mod, "make_neural", lambda kind, model: FakeNeural())
    main(
        [
            "--n-systems",
            "6",
            "--queries-per-kind",
            "5",
            "--neural",
            "ollama",
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert (tmp_path / "results_neural.md").exists() and not (tmp_path / "results.md").exists()
    assert (
        "dense_neural" in json.loads((tmp_path / "results_neural.json").read_text())["retrievers"]
    )


def test_cli_reports_missing_neural_dependency(tmp_path, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "sentence_transformers", None)
    with pytest.raises(SystemExit, match="neural embedder unavailable"):
        main(
            [
                "--n-systems",
                "5",
                "--queries-per-kind",
                "3",
                "--neural",
                "sentence-transformers",
                "--out-dir",
                str(tmp_path),
            ]
        )
    assert not list(tmp_path.iterdir())  # nothing written on failure


def test_make_neural_picks_bge_prefix_only_for_bge():
    assert main_mod.make_neural("sentence-transformers", None).query_prefix.startswith("Represent")
    assert main_mod.make_neural("sentence-transformers", "all-MiniLM-L6-v2").query_prefix == ""
