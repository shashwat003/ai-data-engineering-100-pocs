import re

import pytest
from rag_citation_guard.guard import CITE, check_sentence, guard, passages, split_sentences
from rag_citation_guard.main import evaluate
from rag_citation_guard.synth import CORRUPTIONS, build

from data_gen import generate_knowledge_base

KB = generate_knowledge_base()
STORE = passages(KB.docs)
KEY = next(k for k, s in STORE.items() if any(c.isdigit() for c in s) and len(s.split()) > 8)
SENT = STORE[KEY].rstrip(".")
CITE_OK = f"[{KEY[0]}#{KEY[1]}]"


def test_sentence_splitting_keeps_citations_attached():
    parts = split_sentences(f"First claim here. {CITE_OK} Second claim now. [doc-x#1]")
    assert (
        len(parts) == 2
        and parts[0].endswith(CITE_OK)
        and CITE.findall(parts[1]) == [("doc-x", "1")]
    )


def test_faithful_sentence_passes():
    v = check_sentence(f"{SENT}. {CITE_OK}", STORE)
    assert v.ok and v.reason == "supported" and v.overlap == 1.0


def test_each_failure_mode_is_reported_with_its_reason():
    assert check_sentence(f"{SENT}.", STORE).reason == "no citation"
    assert "does not exist" in check_sentence(f"{SENT}. [doc-ghost#0]", STORE).reason
    other = next(k for k in STORE if k[0] != KEY[0])
    assert not check_sentence(f"{SENT}. [{other[0]}#{other[1]}]", STORE).ok
    k2 = next(k for k, v in STORE.items() if re.search(r"\b\d+ (days|hours|minutes|years)", v))
    s2 = STORE[k2].rstrip(".")
    num = re.search(r"\b(\d+) (?:days|hours|minutes|years)", s2).group(1)
    bad = s2.replace(num + " ", str(int(num) + 7) + " ", 1)
    assert "number" in check_sentence(f"{bad}. [{k2[0]}#{k2[1]}]", STORE).reason
    assert not check_sentence(
        f"Completely unrelated words about weather and football. {CITE_OK}", STORE
    ).ok


def test_paraphrase_with_reordered_clauses_is_not_falsely_flagged():
    parts = [p.strip() for p in SENT.replace(" and ", ",").split(",") if p.strip()]
    reordered = " and ".join(reversed(parts))
    assert check_sentence(f"{reordered}. {CITE_OK}", STORE).ok


def test_answer_level_policy():
    good = f"{SENT}. {CITE_OK}"
    bad = "Access reviews are waived for executive accounts. " + CITE_OK
    assert not guard(f"{good} {good}", STORE)["blocked"]
    mixed = guard(f"{good} {bad} {good}", STORE)
    assert (
        not mixed["blocked"]
        and "waived" not in mixed["text"]
        and mixed["unsupported_share"] == pytest.approx(0.333, abs=1e-3)
    )
    assert guard(f"{bad} {bad} {good}", STORE)["blocked"]
    assert guard("", STORE)["blocked"]


def test_synthetic_labels_are_balanced_and_complete():
    s = build(KB, 600)
    kinds = {x.kind for x in s}
    assert set(CORRUPTIONS) | {"faithful", "paraphrase"} == kinds
    assert 0.4 < sum(not x.faithful for x in s) / len(s) < 0.8
    assert build(KB, 50) == build(KB, 50)


def test_detection_quality_floor_and_no_false_blocks_on_faithful_text():
    r = evaluate(0.6, build(KB, 600), STORE)
    assert r["precision"] == 1.0 and r["recall"] >= 0.95 and r["false_block_rate"] == 0.0
    assert r["by_kind"]["wrong_number"][0] == r["by_kind"]["wrong_number"][1]


def test_lexical_verifier_misses_a_claim_built_from_supported_words():
    twisted = SENT.replace(" must ", " must never ") if " must " in SENT else SENT + " never"
    assert check_sentence(
        f"{twisted}. {CITE_OK}", STORE
    ).ok  # documented limitation: negation is invisible
