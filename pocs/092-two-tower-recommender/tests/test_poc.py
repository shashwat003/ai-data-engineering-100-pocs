import faiss
import numpy as np
import pytest
from two_tower_recommender.data import generate, training_pairs, validation_split
from two_tower_recommender.evaluate import (
    ann_report,
    cooccurrence_scores,
    metrics,
    popularity_scores,
    seen_mask,
    tower_scores,
)
from two_tower_recommender.main import run
from two_tower_recommender.towers import TwoTower, train


@pytest.fixture(scope="module")
def small():
    return generate(n_users=120, n_items=80, n_topics=4, seed=1, per_user=(10, 20))


def test_dataset_invariants(small):
    ds = small
    assert ds.cold_items and all(
        i not in seq for seq in ds.train for i in ds.cold_items
    )  # cold items never appear in training
    for tr, te in zip(ds.train, ds.test, strict=True):
        assert (
            len(te) == 2 and not set(tr) & set(te) and len(set(tr)) == len(tr)
        )  # no repeats, test unseen by the user
    assert ds.pop.sum() == sum(len(s) for s in ds.train)
    again = generate(n_users=120, n_items=80, n_topics=4, seed=1, per_user=(10, 20))
    assert again.train == ds.train and again.test == ds.test


def test_history_features_only_use_earlier_items(small):
    for u, hist, target in training_pairs(small, history=5):
        seq = small.train[u]
        t = seq.index(target)
        assert (
            hist == seq[max(0, t - 5) : t]
            and target not in hist
            and all(h in seq[:t] for h in hist)
        )


def test_validation_split_never_touches_test_items(small):
    v = validation_split(small)
    for u in range(small.n_users):
        assert v.test[u] == small.train[u][-1:] and v.train[u] == small.train[u][:-1]
        assert not set(v.train[u]) & set(small.test[u])


def numerical_grad(model, users, hists, items, log_q, param, idx, eps=1e-5):
    old = param[idx]
    param[idx] = old + eps
    lp, _ = model.loss_and_grads(users, hists, items, log_q)
    param[idx] = old - eps
    lm, _ = model.loss_and_grads(users, hists, items, log_q)
    param[idx] = old
    return (lp - lm) / (2 * eps)


def test_backprop_matches_finite_differences(small):
    model = TwoTower(small, dim=6, hidden=8, temperature=0.5, seed=3)
    rng = np.random.default_rng(0)
    users = rng.integers(0, small.n_users, 12)
    items = rng.integers(0, small.n_items, 12)  # includes duplicate items on purpose
    hists = [small.train[u][:4] for u in users]
    log_q = np.log(rng.random(12) + 0.1)
    loss, grads = model.loss_and_grads(users, hists, items, log_q)
    assert np.isfinite(loss)
    checked = 0
    for p, g in zip(model.params, grads, strict=True):
        nz = np.argwhere(np.abs(g) > 1e-9)
        for idx in nz[:: max(1, len(nz) // 3)][:3]:
            idx = tuple(idx)
            assert numerical_grad(model, users, hists, items, log_q, p, idx) == pytest.approx(
                g[idx], rel=2e-3, abs=1e-7
            ), (p.shape, idx)
            checked += 1
    assert checked >= 20


def test_embeddings_are_unit_norm(small):
    model = TwoTower(small, seed=0)
    assert np.allclose(np.linalg.norm(model.all_item_vectors(), axis=1), 1, atol=1e-5)
    uv = model.user_vectors(np.arange(5), [[1, 2], [], [3], [4, 5, 6], [7]])
    assert np.allclose(np.linalg.norm(uv, axis=1), 1, atol=1e-5)  # also for an empty history


def test_training_reduces_loss_and_beats_popularity(small):
    model = TwoTower(small, dim=16, hidden=32, seed=0)
    losses = train(model, epochs=4, batch=64, seed=0)
    assert losses[-1] < losses[0]
    two = metrics(small, tower_scores(model, small))
    pop = metrics(small, popularity_scores(small))
    assert two["recall@50"] > pop["recall@50"]


def test_metrics_are_correct_on_constructed_scores(small):
    perfect = np.full((small.n_users, small.n_items), -1.0)
    for u, te in enumerate(small.test):
        perfect[u, te] = 5.0
    m = metrics(small, perfect)
    assert (
        m["recall@10"] == 1.0 and m["ndcg@10"] == pytest.approx(1.0) and m["users"] == small.n_users
    )
    worst = -perfect
    assert metrics(small, worst)["recall@10"] < 0.2


def test_seen_items_are_never_recommended(small):
    scores = np.random.default_rng(0).random((small.n_users, small.n_items))
    seen = seen_mask(small)
    masked = np.where(seen, -np.inf, scores)
    top = np.argsort(-masked, axis=1)[:, :10]
    assert not any(seen[u, i] for u in range(small.n_users) for i in top[u])


def test_cooccurrence_beats_popularity_and_cold_items_score_zero(small):
    co, pop = metrics(small, cooccurrence_scores(small)), metrics(small, popularity_scores(small))
    assert co["recall@50"] > pop["recall@50"]
    cold = metrics(small, cooccurrence_scores(small), subset_items=small.cold_items)
    assert (
        cold["recall@50"] == 0.0
    )  # no interaction data, no score: the cold-start gap content features are meant to close


def test_faiss_flat_matches_numpy_and_ann_recall_is_high(small):
    rng = np.random.default_rng(0)
    items = rng.normal(size=(500, 16)).astype("float32")
    users = rng.normal(size=(50, 16)).astype("float32")
    idx = faiss.IndexFlatIP(16)
    idx.add(items)
    _, got = idx.search(users, 10)
    assert (got == np.argsort(-(users @ items.T), axis=1)[:, :10]).all()
    rep = ann_report(items, users, k=10)
    assert (
        rep["flat_exact"]["recall_vs_exact"] == 1.0 and rep["hnsw_ef64"]["recall_vs_exact"] >= 0.9
    )


def test_full_run_selects_epochs_on_validation_and_two_tower_serves_cold_items():
    r = run(seed=0)
    assert r["epochs_used"] in r["validation_recall50_by_epoch"] and r[
        "validation_recall50_by_epoch"
    ][r["epochs_used"]] == max(r["validation_recall50_by_epoch"].values())
    m = r["models"]
    assert (
        m["two-tower"]["all"]["recall@50"] > m["popularity"]["all"]["recall@50"]
        and m["two-tower"]["cold_items_only"]["recall@50"] > 0.1
        and m["item-item co-occurrence"]["cold_items_only"]["recall@50"] == 0
    )
    assert r["ann"]["hnsw_ef64"]["recall_vs_exact"] > 0.95
