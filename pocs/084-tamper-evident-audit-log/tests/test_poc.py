import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tamper_evident_audit_log import merkle
from tamper_evident_audit_log.log import (
    AuditLog,
    Checkpoint,
    audit,
    verify_chain,
    verify_checkpoint,
)
from tamper_evident_audit_log.main import attacks, build, rechain


def leaves(n):
    return [merkle.leaf_hash(bytes([i % 256, i // 256])) for i in range(n)]


def test_merkle_known_properties():
    assert merkle.root([]) == merkle.root([])
    one = leaves(1)
    assert merkle.root(one) == one[0]
    two = leaves(2)
    assert merkle.root(two) == merkle.node_hash(two[0], two[1])
    # leaf and node hashes are domain-separated: a node can never be passed off as a leaf
    assert merkle.leaf_hash(b"ab") != merkle.node_hash(b"a", b"b")


@pytest.mark.parametrize("n", [1, 2, 3, 5, 8, 13, 31, 64])
def test_every_inclusion_proof_verifies_and_fails_for_wrong_leaf_or_index(n):
    ls = leaves(n)
    rt = merkle.root(ls)
    for i in range(n):
        p = merkle.inclusion_proof(ls, i)
        assert merkle.verify_inclusion(ls[i], i, n, p, rt)
        assert not merkle.verify_inclusion(merkle.leaf_hash(b"forged"), i, n, p, rt)
        if n > 1:
            assert not merkle.verify_inclusion(ls[i], (i + 1) % n, n, p, rt)
    assert not merkle.verify_inclusion(ls[0], n, n, [], rt)


def test_all_consistency_proofs_verify_and_tampered_history_is_rejected():
    for n in range(1, 34):
        ls = leaves(n)
        rt = merkle.root(ls)
        for m in range(1, n + 1):
            p = merkle.consistency_proof(ls, m)
            assert merkle.verify_consistency(m, n, merkle.root(ls[:m]), rt, p), (m, n)
            if m < n:
                assert not merkle.verify_consistency(
                    m, n, merkle.root(leaves(m + 1)[:m][::-1]) if m > 1 else b"x" * 32, rt, p
                )
    ls = leaves(20)
    old = merkle.root(ls[:7])
    forged_tail = ls[:7] + [merkle.leaf_hash(b"evil")] * 13
    assert not merkle.verify_consistency(
        7, 20, old, merkle.root(forged_tail), merkle.consistency_proof(ls, 7)
    )


def test_proof_size_is_logarithmic():
    assert len(merkle.inclusion_proof(leaves(1024), 500)) == 10
    assert len(merkle.inclusion_proof(leaves(1000), 500)) <= 10


LOG, KEY = build(60)
ROWS = LOG.rows()
CP = LOG.checkpoint()
PUB = KEY.public_key()


def test_clean_log_verifies_fully():
    a = audit(ROWS, CP, PUB)
    assert a["ok"] and a["entries"] == 60 and a["first_bad_seq"] is None


def test_checkpoint_signature_cannot_be_forged_or_altered():
    assert verify_checkpoint(CP, PUB)
    assert not verify_checkpoint(Checkpoint(CP.size, "00" * 32, CP.signature), PUB)
    assert not verify_checkpoint(Checkpoint(CP.size + 1, CP.root, CP.signature), PUB)
    assert not verify_checkpoint(CP, Ed25519PrivateKey.generate().public_key())
    assert not verify_checkpoint(Checkpoint(CP.size, CP.root, "zz"), PUB)


def test_database_triggers_refuse_update_and_delete():
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        LOG.db.execute("UPDATE entries SET actor='x' WHERE seq=1")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        LOG.db.execute("DELETE FROM entries WHERE seq=1")


EXPECT = {  # attack -> (chain alone detects, chain + checkpoint detects)
    "edit a field (no re-hash)": (True, True),
    "edit a field and re-hash the rest of the chain": (False, True),
    "delete a middle entry and re-number + re-hash": (False, True),
    "delete the last 10 entries": (False, True),
    "swap two entries (no re-hash)": (True, True),
    "insert a forged entry and re-hash": (False, True),
    "replace the whole log with a fresh valid chain": (False, True),
}


@pytest.mark.parametrize("name,tampered", list(attacks(ROWS).items()))
def test_each_attack_matches_the_documented_detection(name, tampered):
    chain_detects = not verify_chain(tampered)[0]
    full_detects = not audit(tampered, CP, PUB)["ok"]
    assert (chain_detects, full_detects) == EXPECT[name], name


def test_appending_after_the_checkpoint_keeps_the_old_checkpoint_valid():
    log, key = build(30)
    cp = log.checkpoint()
    log.append("2026-02-01T00:00:00Z", "ann", "grant", {"object": "new"})
    a = audit(log.rows(), cp, key.public_key())
    assert a["ok"] and a["entries"] == 31
    p = log.consistency(30)
    assert merkle.verify_consistency(
        30, 31, bytes.fromhex(cp.root), bytes.fromhex(log.checkpoint().root), p
    )


def test_inclusion_proof_from_the_log_verifies_against_the_checkpoint():
    leaf, proof, size = LOG.inclusion(17)
    assert merkle.verify_inclusion(leaf, 16, size, proof, bytes.fromhex(CP.root))


def test_rechain_helper_really_produces_a_valid_chain():
    e = list(ROWS)
    e[10] = (*e[10][:4], '{"object": "changed"}', *e[10][5:])
    assert verify_chain(rechain(e, 10))[0] and not verify_chain(e)[0]


def test_empty_log_and_single_entry():
    key = Ed25519PrivateKey.generate()
    log = AuditLog(sqlite3.connect(":memory:"), key)
    cp = log.checkpoint()
    assert cp.size == 0 and audit([], cp, key.public_key())["ok"]
    log.append("t", "a", "x", {})
    assert audit(log.rows(), log.checkpoint(), key.public_key())["ok"]
