from sentiment_slm_vs_classical.data import generate
from sentiment_slm_vs_classical.main import run, score
from sentiment_slm_vs_classical.models import Lexicon, PromptSlm, classical_models

from llm_client import StubClient


def test_data_deterministic_and_balanced():
    a, b = generate(300, 5), generate(300, 5)
    assert a == b and a != generate(300, 6)
    labels = {r["label"] for r in a}
    assert labels == {"negative", "neutral", "positive"}
    assert 0.2 < sum(r["hard"] for r in a) / len(a) < 0.5


def test_noise_rate_close_to_configured():
    clean, noisy = generate(2000, 3, noise=0.0), generate(2000, 3, noise=0.05)
    flipped = sum(c["label"] != n["label"] for c, n in zip(clean, noisy, strict=True)) / 2000
    assert 0.03 < flipped < 0.07


def test_lexicon_fails_hard_cases_where_ngrams_do_not():
    train, test = generate(1500, 1), generate(600, 2)
    ms = classical_models()
    for m in ms.values():
        if hasattr(m, "fit"):
            m.fit([r["text"] for r in train], [r["label"] for r in train])
    r = {n: score(m, test, n) for n, m in ms.items()}
    assert r["lexicon"]["acc_hard"] < 0.4 < 0.8 < r["logreg_bigram"]["acc_hard"]
    assert r["logreg_bigram"]["acc_hard"] >= r["logreg_unigram"]["acc_hard"] - 0.02
    assert Lexicon().predict(["", "great great", "terrible"]) == ["neutral", "positive", "negative"]


def test_prompt_slm_parses_strictly_and_counts_garbage():
    answers = iter(["Positive.", "  negative", "I think it is mixed", "", "NEUTRAL\nbecause"])
    s = PromptSlm(StubClient(lambda p: next(answers)), "m")
    assert s.predict(["a", "b", "c", "d", "e"]) == [
        "positive",
        "negative",
        "neutral",
        "neutral",
        "neutral",
    ]
    assert s.unparsable == 2


def test_prompt_contains_review_and_instruction():
    stub = StubClient("positive")
    PromptSlm(stub, "m").predict(["so good"])
    assert "so good" in stub.calls[0] and "exactly one word" in stub.calls[0]


def test_demo_marks_slm_not_run_without_server():
    r = run(300, 200)
    assert r["slm"]["status"] == "NOT RUN" and "unverified" in r["slm"]["reason"]
    assert len(r["rows"]) == 4 and all(0 <= x["accuracy"] <= 1 for x in r["rows"])
