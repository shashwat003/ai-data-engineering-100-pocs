import json
import random

import pytest
from slm_function_calling_finetune.data import MONTHS, TEMPLATES, generate
from slm_function_calling_finetune.evaluate import decide, evaluate, outcome
from slm_function_calling_finetune.main import gate_audit
from slm_function_calling_finetune.model import (
    Tagger,
    assemble,
    baseline_route,
    baseline_spans,
    pretrain_router,
    train_router,
)
from slm_function_calling_finetune.mutations import MALFORMED, mutate
from slm_function_calling_finetune.tools import TOOLS, parse_tool_call, validate_call

# ---------- schemas and the gate ----------


def call(name, **args):
    return {"name": name, "arguments": args}


GOOD = [
    call("get_weather", city="Berlin", unit="celsius"), call("set_timer", minutes=10), call("send_email", to="a@example.org", subject="hi"),
    call("search_flights", origin="BER", destination="CDG", date="2025-03-14"), call("convert_currency", amount=12.5, from_currency="EUR", to_currency="USD"),
    call("create_event", title="lunch", date="2025-02-28"),
]  # fmt: skip


@pytest.mark.parametrize("c", GOOD)
def test_valid_calls_pass_the_gate(c):
    assert validate_call(c) == [] and parse_tool_call(json.dumps(c))[0] == c


@pytest.mark.parametrize("c", [
    call("get_weather"), call("get_weather", city="Berlin", unit="kelvin"), call("set_timer", minutes=0), call("set_timer", minutes="10"),
    call("set_timer", minutes=10, extra=1), call("send_email", to="nope", subject="x"), call("search_flights", origin="Berlin", destination="CDG", date="2025-03-14"),
    call("search_flights", origin="BER", destination="CDG", date="2025-02-30"), call("convert_currency", amount=-1, from_currency="EUR", to_currency="USD"),
    call("create_event", title="x", date="tomorrow"), {"name": "rm_rf", "arguments": {}}, {"name": "set_timer"},
])  # fmt: skip
def test_invalid_calls_are_rejected_with_a_reason(c):
    assert validate_call(c)


def test_impossible_calendar_dates_are_rejected_even_though_they_look_like_dates():
    errs = validate_call(call("create_event", title="x", date="2025-02-30"))
    assert errs and any("date" in e for e in errs)


@pytest.mark.parametrize("text", MALFORMED)
def test_malformed_model_output_is_rejected_never_repaired(text):
    c, errs = parse_tool_call(text)
    assert c is None and errs


def test_mutation_corpus_is_fully_rejected():
    rng = random.Random(0)
    n = 0
    for e in generate(300, "train", 1):
        if e["gold"] is None:
            continue
        m = mutate(e["gold"], rng)
        if m:
            n += 1
            assert parse_tool_call(json.dumps(m[1]))[0] is None, m
    assert n > 100


def test_gate_audit_summary():
    g = gate_audit(n=300)
    assert (
        g["valid_calls_accepted"] == g["valid_calls"]
        and g["mutations_rejected"] == g["mutations"]
        and g["malformed_rejected"] == g["malformed_outputs"]
    )


def test_every_tool_has_three_disjoint_template_families_and_matching_slots():
    for tool, fams in TEMPLATES.items():
        assert (
            len(fams) == 3
            and not (set(fams[0]) & set(fams[1]))
            and not (set(fams[0]) & set(fams[2]))
        )
        for fam in fams:
            for t in fam:
                slots = {w[1:-1] for w in t.split() if w.startswith("{")}
                assert slots <= set(TOOLS[tool]["properties"]), (tool, t)


# ---------- data ----------


def test_generated_gold_calls_are_schema_valid_and_tags_align():
    for e in generate(400, "train+aug", 3):
        assert len(e["tags"]) == len(e["text"].split())
        if e["gold"]:
            assert validate_call(e["gold"]) == [], e
        else:
            assert e["tool"] == "none" and set(e["tags"]) == {"O"}


def test_generation_is_deterministic_and_splits_use_different_wording():
    assert generate(30, "train", 1) == generate(30, "train", 1)
    a = {e["text"].split()[0] for e in generate(300, "train", 1)}
    b = {e["text"].split()[0] for e in generate(300, "test", 1)}
    assert a != b


def test_none_utterances_are_a_minority_but_present():
    ex = generate(1000, "train", 2)
    share = sum(e["tool"] == "none" for e in ex) / 1000
    assert 0.05 < share < 0.2


# ---------- decoding ----------


def test_assemble_coerces_types_and_leaves_unparsable_values_raw():
    c = assemble("set_timer", {"minutes": "forty five", "label": "tea"})
    assert c["arguments"]["minutes"] == 45
    c = assemble(
        "search_flights", {"origin": "Berlin", "destination": "CDG", "date": f"{MONTHS[2]} 14 2025"}
    )
    assert c["arguments"] == {"origin": "BER", "destination": "CDG", "date": "2025-03-14"}
    bad = assemble("set_timer", {"minutes": "soon"})
    assert bad["arguments"]["minutes"] == "soon" and validate_call(
        bad
    )  # the gate, not the decoder, rejects it


def test_assemble_drops_slots_the_tool_does_not_have():
    assert assemble("set_timer", {"minutes": "5", "city": "Oslo"})["arguments"] == {"minutes": 5}


def test_baseline_matcher_and_regex_slots():
    assert (
        baseline_route("what is the weather in Oslo")[0] == "get_weather"
        and baseline_route("good morning")[0] == "none"
    )
    assert baseline_spans("send_email", "email bo@example.org about x")["to"] == "bo@example.org"
    assert baseline_spans("search_flights", "flights BER CDG 2025-03-14") == {
        "origin": "BER",
        "destination": "CDG",
        "date": "2025-03-14",
    }


# ---------- trained system (small budgets) ----------


@pytest.fixture(scope="module")
def system():
    base = pretrain_router(n=1500, epochs=8)
    tr = generate(200, "train+aug", 7)
    return base, tr, train_router(base, tr, "lora", 0), Tagger().fit(tr)


def test_tuned_system_handles_seen_phrasings(system):
    _, _, router, tagger = system
    r = evaluate("tuned", generate(300, "train", 4242), router, tagger)
    assert r["exact_call_rate"] > 0.8 and r["tool_accuracy"] > 0.9


def test_the_gate_removes_invalid_calls_on_unseen_phrasings_at_the_price_of_clarifications(system):
    _, _, router, tagger = system
    ts = generate(300, "test", 4243)
    free, gated = evaluate("tuned", ts, router, tagger), evaluate("gated", ts, router, tagger)
    assert gated["invalid_call_rate"] == 0
    assert free["invalid_call_rate"] >= gated["invalid_call_rate"] and gated["clarify_rate"] > 0


def test_outcome_taxonomy_covers_each_case():
    gold = {"name": "set_timer", "arguments": {"minutes": 5}}
    ex = {"gold": gold}
    assert outcome(ex, {"kind": "call", "call": gold}) == "correct_call"
    assert (
        outcome(ex, {"kind": "call", "call": call("set_timer", minutes=6)})
        == "wrong_but_valid_call"
    )
    assert outcome(ex, {"kind": "call", "call": call("set_timer", minutes=0)}) == "invalid_call"
    assert outcome(ex, {"kind": "none", "call": None}) == "missed_call"
    assert outcome(ex, {"kind": "clarify", "call": None}) == "clarify"
    assert outcome({"gold": None}, {"kind": "none", "call": None}) == "correct_abstain"
    assert outcome({"gold": None}, {"kind": "call", "call": gold}) == "hallucinated_call"


def test_gated_policy_never_emits_a_schema_invalid_call(system):
    _, _, router, tagger = system
    for e in generate(200, "test", 5):
        d = decide("gated", e, router, tagger)
        if d["kind"] == "call":
            assert validate_call(d["call"]) == []


def test_low_confidence_triggers_clarification(system):
    _, _, router, tagger = system
    e = generate(1, "train", 1)[0]
    assert decide("gated", e, router, tagger, tau=1.01)["kind"] in ("clarify", "none")
