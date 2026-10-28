from graph_rag_knowledge_graph.corpus import build
from graph_rag_knowledge_graph.graph import (
    GraphRetriever,
    build_graph,
    canon,
    extract_relations,
    link_entities,
    relation_plan,
)
from graph_rag_knowledge_graph.main import run, support_recall

C = build(40, seed=3)
R = run(40, seed=3, drop_rates=(0.0, 0.4))
M = R["methods"]


def test_corpus_is_deterministic_and_questions_have_expected_gold_sizes():
    assert build(40, seed=3)["questions"] == C["questions"]
    assert all(len(q["gold"]) == q["hops"] for q in C["questions"])
    assert {q["hops"] for q in C["questions"]} == {1, 2, 3}


def test_extractor_recovers_every_fact_from_all_paraphrases():
    rels, miss = extract_relations(C["passages"])
    truth = {(f["kind"], f["a"], f["b"]) for f in C["facts"]}
    got = {(r["kind"], r["a"], r["b"]) for r in rels}
    assert got == {(k, canon(a), canon(b)) for k, a, b in truth}
    assert len(rels) == len(C["facts"]) and miss == len(C["companies"])  # distractors only


def test_entity_linking_and_aliases():
    rels, _ = extract_relations(C["passages"])
    g = build_graph(rels)
    name = C["companies"][0]
    assert link_entities(f"Who owns {name} Corp today?", g) == [name]
    assert link_entities("nobody known here", g) == []
    assert canon("Acme Corp") == "Acme" and canon("Acme") == "Acme"


def test_relation_plan_orders_hops_innermost_first():
    assert relation_plan(
        "Who is the chief executive of the supplier of the company that acquired X?"
    ) == ["acquired", "supplies", "ceo"]
    assert relation_plan("In which city is the headquarters of the company that acquired X?") == [
        "acquired",
        "hq",
    ]
    assert relation_plan("Tell me about X") == []


def test_plain_retrieval_collapses_on_multi_hop_but_is_decent_on_one_hop():
    assert M["bm25"][1]["all_support"] >= 0.45
    assert M["bm25"][2]["all_support"] < 0.15 and M["bm25"][3]["all_support"] < 0.05
    assert M["hybrid_rrf"][3]["all_support"] < 0.05


def test_graph_with_plan_beats_every_plain_method_on_two_and_three_hops():
    g = M["graph + relation plan, edge loss 0%"]
    for h in (2, 3):
        assert (
            g[h]["all_support"]
            > max(M[m][h]["all_support"] for m in ("bm25", "dense_lsa", "hybrid_rrf")) + 0.3
        )
    assert g[1]["all_support"] > 0.9


def test_graph_quality_degrades_with_extraction_loss():
    a, b = M["graph + relation plan, edge loss 0%"], M["graph + relation plan, edge loss 40%"]
    assert all(b[h]["all_support"] < a[h]["all_support"] for h in (1, 2, 3))


def test_plan_helps_over_bm25_edge_scoring():
    assert (
        M["graph + relation plan, edge loss 0%"][2]["all_support"]
        > M["graph (BM25 edge scoring), edge loss 0%"][2]["all_support"]
    )


def test_support_recall_metric():
    assert support_recall(["a", "b"], {"a", "b"}) == (1.0, True)
    assert support_recall(["a"], {"a", "b"}) == (0.5, False)
    assert support_recall([], {"a"}) == (0.0, False)


def test_unknown_entity_returns_nothing_and_budget_is_respected():
    rels, _ = extract_relations(C["passages"])
    g = GraphRetriever(C["passages"], rels, use_plan=True)
    assert g.search("What is the weather?", k=6) == []
    q = next(x for x in C["questions"] if x["hops"] == 3)
    assert len(g.search(q["q"], k=2)) <= 2
    assert len(set(g.search(q["q"], k=6))) == len(g.search(q["q"], k=6))  # no duplicate passages
