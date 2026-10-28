import os
import shutil

import numpy as np
import pytest

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

from medallion_lakehouse_duckdb.generate import generate_landing  # noqa: E402
from reference_ai_data_platform.main import n_versions  # noqa: E402
from reference_ai_data_platform.mlstep import simulated_label, window_rows  # noqa: E402
from reference_ai_data_platform.platform import Platform, customer_docs  # noqa: E402


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    root = tmp_path_factory.mktemp("plat")
    generate_landing(root / "landing", n_orders=3000)
    batches = sorted(p for p in (root / "landing").iterdir() if p.is_dir())
    p = Platform(root / "platform")
    results = [p.process_batch(b, broken_candidate=(b.name == "b004")) for b in batches]
    yield p, batches, results, root
    p.close()


def test_every_batch_passes_all_lakehouse_gates_including_the_contract(run):
    _, _, results, _ = run
    assert [r.status for r in results] == ["ok"] * 4
    assert all(r.gates_passed == r.gates_total == 8 for r in results)


def test_first_run_ingests_every_file_once(run):
    _, _, results, _ = run
    assert [r.ingested for r in results] == [2, 2, 2, 2]
    assert (
        results[0].skipped == 0 and results[3].skipped == 6
    )  # the platform re-offers earlier batches' files


def test_vector_index_is_incremental_and_covers_every_customer(run):
    p, _, results, _ = run
    assert results[0].vector["embedded"] == results[0].vector["new"] > 100
    assert all(r.vector["embedded"] < 0.3 * 200 for r in results[1:])
    assert p.db.execute("SELECT COUNT(*) FROM docs").fetchone()[0] == len(customer_docs(p.lake.con))


def test_index_matches_a_fresh_rebuild_of_the_same_state(run, tmp_path):
    import sqlite3

    from idempotent_vector_ingestion.store import HashingEmbedder, VectorStore

    p, _, _, _ = run
    fresh = VectorStore(sqlite3.connect(":memory:"), HashingEmbedder())
    fresh.sync(customer_docs(p.lake.con))
    q = "Customer 7 is a"
    assert p.store.search(q, 5) == fresh.search(q, 5)


def test_no_model_is_trained_before_there_is_a_second_window(run):
    _, _, results, _ = run
    assert results[0].models == []


def test_first_model_is_promoted_without_a_champion(run):
    _, _, results, _ = run
    assert results[1].models[0]["promoted"] and results[1].models[0]["champion"] is None


def test_champion_recovers_after_concept_drift_with_a_demonstrated_improvement(run):
    p, _, results, _ = run
    regular = next(m for m in results[3].models if "regular" in m["candidate"])
    assert regular["promoted"] and regular["auc_delta_ci"][0] > 0.1
    assert str(p.champion()) == regular["candidate"].split()[0].lstrip("v")


def test_candidate_with_a_missing_feature_is_rejected_before_scoring(run):
    _, _, results, _ = run
    bad = next(m for m in results[3].models if "missing" in m["candidate"])
    assert not bad["promoted"] and "signature mismatch" in bad["reasons"][0]


def test_labels_are_simulated_and_the_drift_rule_flips_the_amount_effect(run):
    p, _, _, _ = run
    ids, X, _ = window_rows(p.lake.con, 2)
    y1, y2 = simulated_label(ids, X, 2), simulated_label(ids, X, 4)
    big = X[:, 0] > np.median(X[:, 0])
    assert y1[big].mean() > y1[~big].mean() and y2[big].mean() < y2[~big].mean()
    assert (simulated_label(ids, X, 2) == y1).all()  # deterministic


def test_replaying_all_batches_is_a_complete_noop(run):
    p, batches, _, _ = run
    h, v = p.lake.content_hash(), n_versions(p)
    replay = [p.process_batch(b) for b in batches]
    assert all(r.status == "ok" and r.ingested == 0 for r in replay)
    assert all(r.vector["embedded"] == 0 for r in replay) and all(r.models == [] for r in replay)
    assert p.lake.content_hash() == h and n_versions(p) == v


def test_tampered_batch_fails_closed_and_changes_nothing_downstream(run):
    p, batches, _, root = run
    bad = root / "tampered" / "b002"
    shutil.copytree(batches[1], bad)
    f = bad / "orders.csv"
    f.write_text(f.read_text().replace("completed", "shipped", 1))
    before = (
        p.lake.content_hash(),
        p.db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0],
        n_versions(p),
    )
    r = p.process_batch(bad)
    after = (
        p.lake.content_hash(),
        p.db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0],
        n_versions(p),
    )
    assert r.status == "failed" and "BatchConflict" in r.error and before == after


def test_spans_cover_every_stage_and_mark_failures(run):
    p, _, _, _ = run
    names = [s.name for s in p.exporter.get_finished_spans()]
    assert {"platform.batch", "lakehouse.run", "vector.sync", "model.gate"} <= set(names)
    errs = [
        s
        for s in p.exporter.get_finished_spans()
        if s.name == "platform.batch" and "error" in s.attributes
    ]
    assert all("BatchConflict" in s.attributes["error"] for s in errs)


def test_state_survives_a_restart(run):
    p, batches, _, root = run
    champion = p.champion()
    q = Platform(root / "platform")  # same directory: lake, index, registry and ledger are reopened
    try:
        assert str(q.champion()) == str(champion)
        r = q.process_batch(batches[3])
        assert r.status == "ok" and r.ingested == 0 and r.models == []
    finally:
        q.close()
