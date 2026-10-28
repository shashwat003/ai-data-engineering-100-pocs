import pytest
from semantic_cache_gateway.cache import Gateway, SemanticCache, facts
from semantic_cache_gateway.data import make
from semantic_cache_gateway.main import evaluate


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def kw(**o):
    return {"tenant": "t1", "model": "m1", **o}


def test_facts_extracts_numbers_and_names_but_not_question_words():
    assert facts("How do I get a refund for order 1042?") == {"1042"}
    assert facts("Does Atlas work with Beacon?") == {"atlas", "beacon"}
    assert facts("Atlas and Beacon: compatible?") == {"atlas", "beacon"}
    assert facts("what is the warranty") == frozenset()


def test_exact_and_paraphrase_hits_and_miss():
    c = SemanticCache(threshold=0.5)
    c.store_response("How do I get a refund for order 1042?", "A", **kw())
    assert c.lookup("How do I get a refund for order 1042?", **kw())[0] == "A"
    assert c.lookup("What is the process to get a refund for order 1042?", **kw())[0] == "A"
    assert c.lookup("completely unrelated question about weather", **kw())[0] is None


def test_fact_guard_blocks_the_near_miss_that_similarity_alone_accepts():
    q1, q2 = "How do I get a refund for order 1042?", "How do I get a refund for order 2051?"
    unguarded = SemanticCache(threshold=0.7, fact_guard=False)
    unguarded.store_response(q1, "refund-1042", **kw())
    hit, sim = unguarded.lookup(q2, **kw())
    assert hit == "refund-1042" and sim >= 0.7  # a wrong answer served confidently
    guarded = SemanticCache(threshold=0.7)
    guarded.store_response(q1, "refund-1042", **kw())
    assert guarded.lookup(q2, **kw())[0] is None and guarded.stats["rejected_by_fact_guard"] == 1


def test_tenants_models_and_template_versions_are_isolated():
    c = SemanticCache(threshold=0.5)
    c.store_response("What is the warranty for Atlas?", "A", **kw())
    q = "What is the warranty for Atlas?"
    assert c.lookup(q, tenant="t2", model="m1")[0] is None
    assert c.lookup(q, tenant="t1", model="m2")[0] is None
    assert c.lookup(q, tenant="t1", model="m1", template="v2")[0] is None
    assert c.lookup(q, **kw())[0] == "A"


def test_ttl_expiry():
    clock = Clock()
    c = SemanticCache(threshold=0.5, ttl_s=60, clock=clock)
    c.store_response("What is the warranty for Atlas?", "A", **kw())
    clock.t = 59
    assert c.lookup("What is the warranty for Atlas?", **kw())[0] == "A"
    clock.t = 61
    assert (
        c.lookup("What is the warranty for Atlas?", **kw())[0] is None and c.stats["expired"] == 1
    )


def test_lru_eviction_keeps_recently_used_entries():
    c = SemanticCache(threshold=0.9, max_entries=2)
    for i, q in enumerate(["alpha question one", "bravo question two"]):
        c.store_response(q, str(i), **kw())
    c.lookup("alpha question one", **kw())  # touch alpha
    c.store_response("charlie question three", "2", **kw())
    assert (
        c.lookup("alpha question one", **kw())[0] == "0"
        and c.lookup("bravo question two", **kw())[0] is None
    )
    assert c.stats["evicted"] == 1


@pytest.mark.parametrize(
    "bad", ["", "   ", "I can't help with that.", "Error: upstream timeout", "Rate limit exceeded"]
)
def test_failures_and_refusals_are_never_cached(bad):
    c = SemanticCache()
    assert not c.store_response("some question", bad, **kw())
    assert c.lookup("some question", **kw())[0] is None and c.stats["not_cached"] == 1


def test_gateway_counts_calls_and_savings():
    calls = []
    gw = Gateway(lambda p: calls.append(p) or f"answer for: {p}", SemanticCache(threshold=0.7))
    for _ in range(4):
        out, _ = gw.ask("Where is my package for order 1234?")
    assert (
        len(calls) == 1
        and gw.calls == 1
        and gw.tokens_saved
        == 3 * (len("Where is my package for order 1234?".split()) * 4 // 3 + 60)
    )
    assert gw.usd_saved > 0 and out.endswith("1234?")


def test_with_guard_no_false_hits_at_any_threshold_and_hit_rate_falls_as_threshold_rises():
    rows = [evaluate(t, True) for t in (0.5, 0.7, 0.9)]
    assert all(r["false_hit_rate"] == 0.0 for r in rows)
    assert (
        rows[0]["paraphrase_hit_rate"]
        >= rows[1]["paraphrase_hit_rate"]
        >= rows[2]["paraphrase_hit_rate"]
    )
    assert (
        evaluate(0.5, False)["false_hit_rate"] > 0.3
    )  # the unguarded cache serves wrong answers often


def test_dataset_is_deterministic_and_twins_differ_in_facts():
    s, p, t = make(30, seed=2)
    assert (s, p, t) == make(30, seed=2)
    assert all(facts(s[i]) != facts(q) for q, i in t)
    assert all(facts(s[i]) == facts(q) for q, i in p)
