import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from deltalake import DeltaTable
from gdpr_erasure_pipeline import lake, synth
from gdpr_erasure_pipeline.erasure import SLA_DAYS, Pipeline, apply_retention
from gdpr_erasure_pipeline.main import TODAY, run, setup
from gdpr_erasure_pipeline.vectors import KeyStore, TicketStore, file_residue

R = run()


@pytest.fixture
def env(tmp_path):
    pipe, ppl, ev = setup(tmp_path, n_people=12)
    return pipe, ppl, ev, tmp_path


def test_before_state_has_the_subject_in_files_and_old_versions():
    b = R["before"]
    assert (
        b["lake_rows"] > 0
        and b["tickets"] == 2
        and len(b["old_versions_with_subject"]) >= 5
        and b["residue_files_before"] > 0
    )


def test_delete_without_vacuum_is_not_erasure():
    n = R["naive_delete_only"]
    assert n["rows_visible"] == 0 and n["residue_hits"] > 0 and len(n["old_versions_exposing"]) >= 5


def test_full_erasure_leaves_no_residue_and_no_readable_history():
    a = R["after_erasure"]
    assert a == {
        "status": "completed",
        "residue_hits": 0,
        "readable_old_versions": [],
        "lake_rows_left": 0,
        "tickets_left": 0,
    }


def test_erasure_resumes_after_a_crash_and_is_idempotent():
    assert R["resumed_after_crash"] > 0 and R["second_request"] == "already_completed"


def test_other_subjects_and_search_are_unaffected():
    assert all(R["collateral"].values())


def test_backups_ticket_data_is_crypto_shredded_but_unencrypted_lake_backup_is_not():
    b = R["backups"]
    assert (
        b["ticket_backup_readable_for_subject"] == 0
        and b["ticket_backup_plaintext_hits"] == 0
        and b["lake_backup_still_has_subject"] is True
    )


def test_legal_hold_and_retention_interplay():
    assert R["legal_hold"] == "refused_legal_hold"
    assert (
        R["retention"]["rows_deleted"] > 0 and R["retention"]["expired_rows_left_without_hold"] == 0
    )


def test_audit_chain_is_valid_and_tampering_is_detected(env):
    pipe, ppl, _ev, _ = env
    pipe.erase(ppl[1]["user_id"], TODAY, [ppl[1]["email"]])
    assert pipe.verify_audit_chain()
    lines = pipe.audit_file.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["at"] = "1999-01-01"
    pipe.audit_file.write_text("\n".join([json.dumps(rec), *lines[1:]]) + "\n")
    assert not pipe.verify_audit_chain()


def test_audit_and_state_hold_no_personal_data(env):
    pipe, ppl, _ev, _ = env
    p = ppl[2]
    pipe.erase(p["user_id"], TODAY, [p["email"], p["name"]])
    text = pipe.audit_file.read_text() + pipe.req_file.read_text()
    for needle in (p["user_id"], p["email"], p["name"]):
        assert needle not in text
    assert pipe.pseudonym(p["user_id"]) in text


def test_unsafe_subject_ids_are_rejected_before_reaching_a_delete_predicate(env):
    pipe, *_ = env
    with pytest.raises(ValueError, match="unsafe"):
        lake.erase_subject(pipe.lake_path, "u1' OR '1'='1")
    before = lake.count(pipe.lake_path)
    assert lake.count(pipe.lake_path) == before
    with pytest.raises(ValueError, match="unsafe"):
        apply_retention(pipe.lake_path, TODAY, 30, holds={"x'; DROP"})


def test_crash_after_lake_step_does_not_repeat_the_lake_delete(env):
    pipe, ppl, _ev, _ = env
    s = ppl[3]
    with pytest.raises(RuntimeError):
        pipe.erase(s["user_id"], TODAY, [s["email"]], fail_after_lake=True)
    v = DeltaTable(str(pipe.lake_path)).version()
    out = pipe.erase(s["user_id"], TODAY, [s["email"]])
    assert (
        out["status"] == "completed" and DeltaTable(str(pipe.lake_path)).version() == v
    )  # vacuum adds no commit; no second delete


def test_leaked_plaintext_copy_makes_verification_fail(env):
    pipe, ppl, _ev, _ = env
    s = ppl[4]
    (pipe.tickets.dir / "search_cache.json").write_text(
        json.dumps({"q": s["email"]})
    )  # a derived cache nobody registered
    out = pipe.erase(s["user_id"], TODAY, [s["email"]])
    assert out["status"] == "failed_verification" and out["residue_hits"] >= 1


def test_overdue_requests_are_reported_after_the_sla(env):
    pipe, ppl, _ev, _ = env
    with pytest.raises(RuntimeError):
        pipe.erase(ppl[5]["user_id"], TODAY, fail_after_lake=True)
    assert (
        pipe.overdue(TODAY + timedelta(days=SLA_DAYS - 1)) == []
        and len(pipe.overdue(TODAY + timedelta(days=SLA_DAYS + 1))) == 1
    )


def test_pipeline_requires_a_real_salt(tmp_path):
    with pytest.raises(ValueError):
        Pipeline(tmp_path / "s", tmp_path / "l", None, salt=b"short")  # type: ignore[arg-type]


def test_ticket_store_is_encrypted_at_rest_and_key_destruction_is_final():
    with tempfile.TemporaryDirectory() as t:
        keys = KeyStore(Path(t) / "k.json")
        ts = TicketStore(Path(t) / "d", keys)
        ts.add("a-1", "sub-a", "Ana Adams (ana@example.com) cannot log in")
        assert b"ana@example.com" not in ts.file.read_bytes()
        assert ts.search("cannot log in", 1)[0][0] == "a-1"
        snap = ts.snapshot(Path(t) / "snap")
        assert keys.destroy("sub-a") is True and keys.destroy("sub-a") is False
        assert (
            ts.search("cannot log in", 1) == []
            and TicketStore.readable_in(snap, keys, "sub-a") == 0
        )


def test_file_residue_scanner_finds_planted_values(tmp_path):
    (tmp_path / "x.bin").write_bytes(b"...ana@example.com...")
    assert file_residue(tmp_path, ["ana@example.com", "nobody"]) == [("x.bin", "ana@example.com")]


def test_synthetic_data_is_deterministic():
    assert synth.people(5) == synth.people(5) and synth.events(synth.people(5), 20) == synth.events(
        synth.people(5), 20
    )
    assert (
        date.fromisoformat(max(e["event_date"] for e in synth.events(synth.people(5), 50))) <= TODAY
    )


def test_file_residue_ignores_ids_inside_random_ciphertext_but_not_delimited_plaintext(tmp_path):
    (tmp_path / "blob.jsonl").write_text(
        '{"blob": "gAAAAABkXu005Qzv9=="}\n'
    )  # id embedded in a random base64 run
    assert file_residue(tmp_path, ["u005"]) == []
    (tmp_path / "leak.json").write_text('{"subject": "u005"}')
    (tmp_path / "leak2.txt").write_text("user u005, ana")
    assert file_residue(tmp_path, ["u005"]) == [("leak.json", "u005"), ("leak2.txt", "u005")]
