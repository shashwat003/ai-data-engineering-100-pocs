import sqlite3

import numpy as np
import pytest
from idempotent_vector_ingestion.main import edit, make_corpus, para_chunker
from idempotent_vector_ingestion.store import HashingEmbedder, VectorStore, chunk, chunk_key


def new_store(version="v1", chunker=para_chunker):
    return VectorStore(sqlite3.connect(":memory:"), HashingEmbedder(version), chunker)


DOC = "alpha beta gamma\ndelta epsilon zeta\neta theta iota"


def test_embedder_is_deterministic_normalised_and_counts_calls():
    e = HashingEmbedder()
    a, b = e.embed(["hello world", "hello world"])
    assert np.array_equal(a, b) and e.calls == 2 and np.linalg.norm(a) == pytest.approx(1.0)
    assert np.allclose(e.embed([""]), 0)  # empty text: zero vector, no crash
    s1 = float(e.embed(["encryption key rotation"])[0] @ e.embed(["key rotation policy"])[0])
    s2 = float(e.embed(["encryption key rotation"])[0] @ e.embed(["quarterly invoice totals"])[0])
    assert s1 > s2


def test_chunker_and_keys():
    assert chunk("a b c d e", size=2) == ["a b", "c d", "e"]
    assert chunk("", 3) == [] and chunk("a b", 5, overlap=2) == ["a b"]
    assert chunk_key("v1", "x") != chunk_key("v2", "x") and chunk_key("v1", "x") == chunk_key(
        "v1", "x"
    )


def test_first_ingest_embeds_every_chunk_once_and_rerun_is_free():
    s = new_store()
    assert s.upsert("d", DOC) == {"doc": "d", "status": "new", "embedded": 3}
    assert s.upsert("d", DOC) == {"doc": "d", "status": "unchanged", "embedded": 0}
    assert s.emb.calls == 3 and s.stats() == {"docs": 1, "chunk_refs": 3, "vectors": 3}


def test_editing_one_paragraph_embeds_exactly_one_chunk_and_removes_the_old_vector():
    s = new_store()
    s.upsert("d", DOC)
    r = s.upsert("d", DOC.replace("zeta", "omega"))
    assert r["status"] == "updated" and r["embedded"] == 1
    assert s.stats()["vectors"] == 3  # old chunk garbage-collected, no orphans
    assert not any("zeta" in t for (t,) in s.db.execute("SELECT text FROM vectors"))


def test_identical_chunks_are_shared_and_survive_until_the_last_reference_goes():
    s = new_store()
    s.upsert("a", "same text here\nonly a")
    s.upsert("b", "same text here\nonly b")
    assert s.stats()["vectors"] == 3 and s.emb.calls == 3  # shared paragraph embedded once
    s.delete("a")
    assert s.stats() == {"docs": 1, "chunk_refs": 2, "vectors": 2}
    assert s.search("same text here", 1)[0][0] == "b"
    s.delete("b")
    assert s.stats() == {"docs": 0, "chunk_refs": 0, "vectors": 0} and s.search("anything") == []


def test_model_version_change_forces_reembedding():
    db = sqlite3.connect(":memory:")
    VectorStore(db, HashingEmbedder("v1"), para_chunker).upsert("d", DOC)
    s2 = VectorStore(db, HashingEmbedder("v2"), para_chunker)
    r = s2.upsert("d", DOC)
    assert r["embedded"] == 3 and r["status"] == "updated"
    assert {v for (v,) in db.execute("SELECT version FROM vectors")} == {
        "v2"
    }  # old-version vectors collected


def test_sync_deletes_vanished_documents_and_reports_counts():
    s = new_store()
    s.sync({"a": DOC, "b": DOC + "\nextra line"})
    res = s.sync({"a": DOC})
    assert res["deleted"] == 1 and res["embedded"] == 0 and s.stats()["docs"] == 1


def test_crash_during_upsert_leaves_the_previous_version_intact(monkeypatch):
    s = new_store()
    s.upsert("d", DOC)
    before = s.db.execute("SELECT key FROM doc_chunks ORDER BY pos").fetchall()
    monkeypatch.setattr(s, "_gc", lambda: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        s.upsert("d", DOC.replace("alpha", "ALPHA"))
    assert (
        s.db.execute("SELECT key FROM doc_chunks ORDER BY pos").fetchall() == before
    )  # rolled back


def test_incremental_state_equals_fresh_rebuild_after_many_edits():
    import random

    rng = random.Random(9)
    corpus = make_corpus(40)
    s = new_store()
    s.sync(corpus)
    for _ in range(5):
        corpus = edit(corpus, rng, 4, 2, 2)
        s.sync(corpus)
    fresh = new_store()
    fresh.sync(corpus)
    inc = sorted(s.db.execute("SELECT key FROM vectors").fetchall())
    assert inc == sorted(fresh.db.execute("SELECT key FROM vectors").fetchall())
    for q in ("retention system4", "failover audit policy", "revised"):
        assert s.search(q, 5) == fresh.search(q, 5)


def test_incremental_cost_is_far_below_full_reembed():
    import random

    rng = random.Random(1)
    corpus = make_corpus(60)
    s = new_store()
    s.sync(corpus)
    res = s.sync(edit(corpus, rng, 3, 0, 0))
    assert 0 < res["embedded"] <= 3 and res["unchanged"] >= 57
