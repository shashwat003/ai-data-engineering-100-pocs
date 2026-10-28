import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from data_gen import generate_knowledge_base
from eval_kit import (
    bootstrap_ci,
    contains_answer,
    evaluate_run,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    support_score,
)
from llm_client import LLMError, OllamaClient, StubClient
from pocs_common import (
    BudgetExceeded,
    CostLedger,
    ModelPrice,
    load_env_file,
    markdown_table,
    percentile,
    stable_normal,
    stable_unit,
)
from retrieval_kit import (
    BM25Index,
    BM25Retriever,
    DenseRetriever,
    HybridRetriever,
    LSAEmbedder,
    reciprocal_rank_fusion,
    tokenize,
    weighted_fusion,
)

# ---- pocs_common


def test_stable_random_is_deterministic_and_in_range():
    assert stable_unit("a", 1) == stable_unit("a", 1)
    assert stable_unit("a", 1) != stable_unit("a", 2)
    assert 0 <= stable_unit("x") < 1
    assert isinstance(stable_normal("x"), float)


def test_cost_ledger_tracks_and_enforces_budget():
    prices = {"m": ModelPrice(1.0, 2.0)}
    ledger = CostLedger(prices, budget_usd=0.000010)
    assert ledger.record("m", 1_000_000, 0, enforce=False) == pytest.approx(1.0)
    assert ledger.exhausted()
    with pytest.raises(BudgetExceeded):
        CostLedger(prices, budget_usd=0.5).record("m", 1_000_000, 0, enforce=True)
    with pytest.raises(KeyError):
        ledger.record("unknown", 1, 1)
    assert ledger.by_model() == {"m": pytest.approx(1.0)}


def test_percentile_and_markdown_and_env(tmp_path):
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([5], 95) == 5
    assert markdown_table(["a", "b"], [[1, 0.5]]).splitlines()[-1] == "| 1 | 0.500 |"
    env = tmp_path / ".env"
    env.write_text("# c\nK=v\nQ='x y'\n\nbad line\n")
    assert load_env_file(env) == {"K": "v", "Q": "x y"}
    assert load_env_file(tmp_path / "missing") == {}


# ---- eval_kit


def test_ranking_metrics():
    ranked = ["x", "a", "y"]
    assert recall_at_k(ranked, {"a"}, 1) == 0.0
    assert recall_at_k(ranked, {"a"}, 2) == 1.0
    assert reciprocal_rank(ranked, {"a"}) == 0.5
    assert reciprocal_rank(ranked, {"zzz"}) == 0.0
    assert ndcg_at_k(["a"], {"a"}) == pytest.approx(1.0)
    assert ndcg_at_k(ranked, {"a"}) == pytest.approx(1 / 1.5849625, rel=1e-4)


def test_evaluate_run_and_bootstrap():
    m = evaluate_run({"q1": ["a"], "q2": ["b", "a"]}, {"q1": {"a"}, "q2": {"a"}})
    assert m.aggregate["recall@1"] == 0.5
    assert m.aggregate["mrr@10"] == pytest.approx(0.75)
    lo, hi = bootstrap_ci([0.0, 1.0] * 50, n_boot=200, seed=1)
    assert 0.3 < lo < 0.5 < hi < 0.7
    assert bootstrap_ci([1.0] * 10, n_boot=50) == (1.0, 1.0)


def test_answer_checks():
    assert contains_answer("It is kept for 30 days.", "30 days")
    assert not contains_answer("It is kept for 130 days.", "30 days")
    ctx = ["Backups are kept for 30 days in vault-eu."]
    assert support_score("kept for 30 days", ctx) == 1.0
    assert support_score("kept forever in the moon vault", ctx) <= 0.5


# ---- retrieval_kit


def test_tokenize_drops_stopwords():
    assert tokenize("What is the RB-1042 code?") == ["rb", "1042", "code"]


def test_bm25_ranks_matching_document_first_and_is_deterministic():
    idx = BM25Index().fit(["d1", "d2", "d3"], ["red apple pie", "green pear tart", "red pear jam"])
    assert idx.search("pear tart", 3)[0][0] == "d2"
    assert idx.search("zzz") == []
    assert idx.search("red", 3) == idx.search("red", 3)


def test_fusion_math():
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]], k=60)
    assert [d for d, _ in fused][0] == "b"
    assert dict(fused)["a"] == pytest.approx(1 / 61)
    w = weighted_fusion([[("a", 10.0), ("b", 0.0)], [("b", 1.0), ("c", 0.5)]], [0.5, 0.5])
    assert w[0][0] in {"a", "b"} and dict(w)["c"] == pytest.approx(0.25)
    with pytest.raises(ValueError):
        weighted_fusion([[("a", 1.0)]], [0.5, 0.5])


def test_lsa_retrieval_is_robust_to_a_typo():
    ids = ["fruit", "cars", "space"]
    texts = [
        "apple banana orchard harvest",
        "engine wheel garage mechanic",
        "rocket orbit planet moon",
    ]
    emb = LSAEmbedder(dims=3)
    dense = DenseRetriever(ids, texts, emb)
    assert dense.search("harvset apple", 1)[0][0] == "fruit"
    hybrid = HybridRetriever(BM25Retriever(ids, texts), dense, mode="weighted", alpha=0.5)
    assert hybrid.search("mechanic garage", 1)[0][0] == "cars"
    with pytest.raises(ValueError):
        HybridRetriever(BM25Retriever(ids, texts), dense, mode="nope")


# ---- data_gen


def test_knowledge_base_is_deterministic_and_well_formed():
    a = generate_knowledge_base(n_systems=6, queries_per_kind=10, seed=3)
    b = generate_knowledge_base(n_systems=6, queries_per_kind=10, seed=3)
    c = generate_knowledge_base(n_systems=6, queries_per_kind=10, seed=4)
    assert a.fingerprint() == b.fingerprint() != c.fingerprint()
    assert len(a.docs) == 30 and len(a.qa) == 40
    ids = {d.doc_id for d in a.docs}
    assert all(q.gold_doc_id in ids for q in a.qa)
    by_id = {d.doc_id: d for d in a.docs}
    assert all(contains_answer(by_id[q.gold_doc_id].text, q.gold_answer) for q in a.qa)
    para = next(q for q in a.qa if q.kind == "paraphrase")
    assert para.question != next(q for q in a.qa if q.kind == "natural").question
    with pytest.raises(ValueError):
        generate_knowledge_base(n_systems=0)


# ---- llm_client


def test_stub_client_records_calls():
    stub = StubClient(lambda p: p.upper())
    r = stub.generate("hi", model="m")
    assert r.text == "HI" and stub.calls == ["hi"]


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/generate":
            out = {"response": f"echo:{body['prompt']}", "prompt_eval_count": 7, "eval_count": 3}
        elif self.path == "/api/embed":
            out = {"embeddings": [[1.0, 0.0] for _ in body["input"]]}
        else:
            out = {"oops": 1}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def test_ollama_client_against_local_stub_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = OllamaClient(f"http://127.0.0.1:{server.server_port}", timeout=5)
        r = client.generate("ping", model="m")
        assert (r.text, r.input_tokens, r.output_tokens) == ("echo:ping", 7, 3)
        assert client.embed(["a", "b"], model="m") == [[1.0, 0.0], [1.0, 0.0]]
    finally:
        server.shutdown()


def test_ollama_client_reports_unreachable_server():
    with pytest.raises(LLMError):
        OllamaClient("http://127.0.0.1:1", timeout=1).generate("x", model="m")


# ---- embedders and Ollama discovery endpoints

import numpy as np  # noqa: E402

from llm_client import model_matches  # noqa: E402
from retrieval_kit import CachedEmbedder, OllamaEmbedder, SentenceTransformerEmbedder  # noqa: E402


class _FakeModel:
    def __init__(self):
        self.seen = []

    def encode(self, texts, batch_size=32, show_progress_bar=False):
        self.seen.append(list(texts))
        return np.array([[float(len(t)), 1.0] for t in texts])


def test_sentence_transformer_embedder_with_injected_model():
    model = _FakeModel()
    emb = SentenceTransformerEmbedder("x", query_prefix="Q: ", model=model)
    docs = emb.encode(["abc"])
    queries = emb.encode_queries(["abc"])
    assert model.seen == [["abc"], ["Q: abc"]]  # prefix applied to queries only
    assert np.allclose(np.linalg.norm(docs, axis=1), 1.0) and not np.allclose(docs, queries)
    assert emb.describe()["model"] == "x"


def test_sentence_transformer_embedder_missing_package_message(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "sentence_transformers", None)
    with pytest.raises(ImportError, match="requirements-neural.txt"):
        SentenceTransformerEmbedder("x").fit(["a"])


def test_cached_embedder_embeds_each_text_once():
    model = _FakeModel()
    cached = CachedEmbedder(SentenceTransformerEmbedder("x", query_prefix="Q: ", model=model))
    cached.encode(["a", "b", "a"])
    cached.encode(["a"])
    cached.encode_queries(["a"])
    cached.encode_queries(["a"])
    assert cached.calls == 3  # a, b (docs) + "Q: a" (query); repeats are cache hits
    assert cached.describe()["kind"] == "sentence-transformers"


class _EmbedClient:
    def embed(self, texts, *, model):
        return [[3.0, 4.0] for _ in texts]


def test_ollama_embedder_normalises():
    v = OllamaEmbedder(_EmbedClient(), "m").encode(["a", "b"])
    assert np.allclose(v, [[0.6, 0.8], [0.6, 0.8]])


def test_ollama_discovery_endpoints_and_model_matching():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = {
                "/api/version": {"version": "9.9.9"},
                "/api/tags": {"models": [{"name": "a:latest"}]},
            }
            data = json.dumps(body[self.path]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = OllamaClient(f"http://127.0.0.1:{server.server_port}", timeout=5)
        assert client.version() == "9.9.9"
        assert client.list_models() == [{"name": "a:latest"}]
    finally:
        server.shutdown()
    assert model_matches("a:latest", "a") and model_matches("llama3.2:3b", "llama3.2:3b")
    assert not model_matches("a:latest", "a:7b") and not model_matches("b:latest", "a")


# ---- shared warehouse generator

import sqlite3  # noqa: E402

from data_gen.warehouse import build_warehouse  # noqa: E402


def test_warehouse_is_deterministic_consistent_and_fictional(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    counts = build_warehouse(a, seed=9, n_customers=30, n_orders=120)
    assert counts["customers"] == 30 and counts["orders"] == 120 and counts["order_items"] >= 120
    assert build_warehouse(b, seed=9, n_customers=30, n_orders=120) == counts
    dump = lambda p: list(sqlite3.connect(p).iterdump())  # noqa: E731
    assert dump(a) == dump(b)
    con = sqlite3.connect(a)
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert all(e.endswith("@example.com") for (e,) in con.execute("SELECT email FROM customers"))
    other = tmp_path / "c.db"
    build_warehouse(other, seed=10, n_customers=30, n_orders=120)
    assert dump(other) != dump(a)
