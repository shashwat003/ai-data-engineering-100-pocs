import random

import pytest
from slm_pii_redaction.data import CONFUSERS, dataset, make_text
from slm_pii_redaction.evaluate import run, score
from slm_pii_redaction.redact import MAX_CHARS, detect, redact
from slm_pii_redaction.tagger import Tagger, tokenize

TRAIN = dataset(800, 1, False)
TAG = Tagger(identity=False).fit(TRAIN)
R = run(n_train=800, n_test=250)


def test_patterns_catch_all_structured_pii_with_no_false_positives():
    for lab in ("EMAIL", "PHONE", "CREDIT_CARD", "IBAN", "US_SSN"):
        m = R["unseen_names"]["per_type"][lab]
        assert m["precision"] == m["recall"] == 1.0, lab


def test_tagger_generalises_to_names_never_seen_in_training_when_sentences_are_familiar():
    assert (
        R["unseen_names"]["per_type"]["PERSON"]["f1"] >= 0.9
        and R["unseen_names"]["char_leak_rate"] < 0.05
    )
    assert R["unseen_names"]["per_type"]["ADDRESS"]["f1"] >= 0.95


def test_person_tagging_does_not_transfer_to_new_sentence_structures():
    """The honest limit: with unseen names AND unseen templates the small tagger is weak. Pinned on purpose."""
    new = R["unseen_names_and_templates"]
    assert new["per_type"]["PERSON"]["f1"] < 0.6 and new["char_leak_rate"] > 0.1
    for lab in (
        "EMAIL",
        "PHONE",
        "CREDIT_CARD",
        "IBAN",
        "US_SSN",
    ):  # validated patterns do not care about sentence structure
        assert new["per_type"][lab]["f1"] == 1.0


def test_identity_features_memorise_names_which_is_why_they_are_off():
    ident = R["identity_features"]
    assert (
        ident["seen_names"]["per_type"]["PERSON"]["recall"]
        - ident["unseen_names"]["per_type"]["PERSON"]["recall"]
        > 0.1
    )
    assert (
        R["unseen_names"]["per_type"]["PERSON"]["recall"]
        > ident["unseen_names"]["per_type"]["PERSON"]["recall"]
    )


def test_patterns_alone_leave_names_and_addresses_exposed():
    assert (
        R["patterns_only"]["char_leak_rate"] > 0.3
        and R["patterns_only"]["per_type"]["PERSON"]["recall"] == 0
    )


def test_capitalised_non_pii_words_are_not_redacted():
    assert R["unseen_names"]["over_redaction_rate"] < 0.01
    for c in CONFUSERS:
        out, spans = redact(f"The {c} release was approved. No personal data here.", TAG)
        assert spans == [] and c in out


def test_structured_validators_reject_look_alikes():
    out, spans = redact("order 1234567890123456 tracking 4111 1111 1111 1112 ref 555-12-3456x", TAG)
    assert not any(lab == "CREDIT_CARD" for _, _, lab in spans)


def test_email_is_one_span_not_a_name_plus_a_domain():
    text = "write to Ana.Ng@example.com today"
    spans = detect(text, TAG)
    assert [(text[s:e], lab) for s, e, lab in spans] == [("Ana.Ng@example.com", "EMAIL")]


def test_typed_redaction_removes_every_structured_value():
    rng = random.Random(3)
    for _ in range(60):
        text, truth = make_text(rng, True)
        out, _ = redact(text, TAG)
        for s, e, lab in truth:
            if lab not in ("PERSON", "ADDRESS"):
                assert text[s:e] not in out


def test_pseudonyms_are_stable_per_value_salt_dependent_and_not_the_value():
    t = "Dear Aziz Bela, your order from Berlin has shipped. Dear Aziz Bela, thank you."
    a, _ = redact(t, TAG, mode="pseudonym", salt=b"salt-one-1")
    b, _ = redact(t, TAG, mode="pseudonym", salt=b"salt-two-2")
    assert a != b and "Aziz" not in a
    tags = [w for w in a.split() if w.startswith("[PERSON_")]
    assert len(tags) >= 2 and tags[0].rstrip(",.") == tags[-1].rstrip(",.")


def test_pseudonym_mode_requires_a_real_salt_and_valid_mode():
    with pytest.raises(ValueError):
        redact("x", TAG, mode="pseudonym", salt=b"")
    with pytest.raises(ValueError):
        redact("x", TAG, mode="reversible")


@pytest.mark.parametrize(
    "text", ["", "   ", "日本語のテキスト", "💳 4111 1111 1111 1111 💳", "a" * 5000]
)
def test_odd_inputs_do_not_crash(text):
    out, _ = redact(text, TAG)
    assert isinstance(out, str)


def test_oversized_input_is_refused():
    with pytest.raises(ValueError, match="too large"):
        redact("a" * (MAX_CHARS + 1), TAG)


def test_known_limitations_are_pinned_so_changes_are_noticed():
    out, _ = redact("reach me at john [at] example [dot] com", TAG)
    assert "john [at] example [dot] com" in out  # obfuscated email is NOT caught
    out, _ = redact("my friend maria gonzalez called", TAG)
    assert "maria gonzalez" in out  # lowercase names are NOT caught (capitalisation is a feature)


def test_training_is_deterministic():
    a, b = Tagger(identity=False).fit(TRAIN), Tagger(identity=False).fit(TRAIN)
    txt = dataset(5, 9, True)
    assert [a.predict_spans(t) for t, _ in txt] == [b.predict_spans(t) for t, _ in txt]


def test_tokenizer_offsets_roundtrip():
    t = "Ship to Ana Ng, 12 Baker Street."
    assert all(t[s:e] == w for s, e, w in tokenize(t))


def test_scoring_function_counts_misses():
    data = [("Call 555-123-4567 now", [(5, 17, "PHONE")])]
    assert score(data, None)["per_type"]["PHONE"]["recall"] == 1.0
    assert (
        score([("Call 555-123-4567 now", [(5, 17, "PHONE"), (0, 4, "PERSON")])], None)["per_type"][
            "PERSON"
        ]["recall"]
        == 0.0
    )
