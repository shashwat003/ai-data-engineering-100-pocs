import duckdb
from pii_scanner_classifier.data import TRUTH, build
from pii_scanner_classifier.main import score
from pii_scanner_classifier.scanner import (
    Finding,
    classify_column,
    free_text_pii,
    mask_value,
    q,
    scan,
)

CON = build(400)


def test_every_column_is_classified_correctly_on_the_synthetic_warehouse():
    fs = scan(CON, 300)
    assert {(f.table, f.column) for f in fs} == set(TRUTH)
    s = score(fs)
    assert s["precision"] == 1.0 and s["recall"] == 1.0 and s["wrong"] == []


def test_auto_mask_tier_excludes_name_only_signals():
    fs = {(f.table, f.column): f for f in scan(CON, 300)}
    assert (
        fs[("customers", "full_name")].confidence == 0.5
        and fs[("customers", "full_name")].evidence == "name hint only"
    )
    assert (
        fs[("customers", "email")].confidence >= 0.8
        and fs[("payments", "card_no")].confidence >= 0.8
    )


def test_values_beat_missing_hints_and_hints_alone_stay_low():
    cls, conf, ev = classify_column("col_a", [f"u{i}@x.example" for i in range(50)])
    assert cls == "email" and conf >= 0.6 and "name hint" not in ev
    cls, conf, _ = classify_column("legacy_phone", [f"{i:016x}" for i in range(50)])
    assert cls == "phone" and conf == 0.3  # name says phone, values disagree: low confidence
    assert classify_column("notes", ["hello", "world"]) == (None, 0.0, "no signal")


def test_look_alikes_are_not_pii():
    assert classify_column("tracking_number", [f"1Z{1234567890 + i}" for i in range(50)])[0] is None
    assert (
        classify_column("order_ref", [str(1_000_000_000 + i * 7919) for i in range(50)])[0] is None
    )  # 10-digit numbers look like phones
    assert (
        classify_column("signup_date", [f"2024-01-{i % 28 + 1:02d}" for i in range(50)])[0] is None
    )  # a date is not a DOB without the name


def test_checksum_prevents_random_digit_columns_from_being_cards():
    import random

    rng = random.Random(1)
    rand = [str(rng.randint(10**15, 10**16 - 1)) for _ in range(500)]
    assert classify_column("account_number", rand)[0] is None
    assert (
        classify_column("card_no", rand)[0] != "credit_card"
        or classify_column("card_no", rand)[1] < 0.8
    )


def test_nulls_and_empty_columns():
    assert classify_column("email", [None, "", "  "])[0:2] == (
        "email",
        0.3,
    )  # only the name remains
    assert classify_column("x", [])[0] is None


def test_sampling_size_does_not_change_the_answer_on_homogeneous_columns():
    a = {(f.table, f.column): f.pii_class for f in scan(CON, 20)}
    b = {(f.table, f.column): f.pii_class for f in scan(CON, 400)}
    assert a == b


def test_findings_never_contain_values():
    for f in scan(CON, 50):
        assert isinstance(f, Finding)
        assert "@" not in f.evidence and "4111" not in f.evidence


def test_free_text_scan_counts_embedded_pii():
    c = duckdb.connect()
    c.execute("CREATE TABLE t (body VARCHAR)")
    c.execute(
        "INSERT INTO t VALUES ('write to a@b.example please'), ('nothing here'), ('call +1 555 123 4567 or a@b.example')"
    )
    assert free_text_pii(c, "t", "body") == {"email": 2, "phone": 1}


def test_masking_keeps_only_what_is_safe():
    assert mask_value("email", "person12@mail.example") == "p***@mail.example"
    assert mask_value("credit_card", "4111 1111 1111 1111") == "************1111"
    assert mask_value("person_name", "Ana Ng") == "[MASKED]"


def test_hostile_identifiers_are_quoted_not_executed():
    c = duckdb.connect()
    c.execute(f"CREATE TABLE {q('t;drop table x;--')} ({q('co"l')} VARCHAR)")
    c.execute(f"INSERT INTO {q('t;drop table x;--')} VALUES ('a@b.example')")
    fs = scan(c, 10)
    assert len(fs) == 1 and fs[0].pii_class == "email"
