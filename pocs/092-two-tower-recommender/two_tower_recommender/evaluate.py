"""Offline evaluation with a temporal holdout, plus baselines (popularity, item-item co-occurrence, SVD) and ANN retrieval via FAISS."""

from __future__ import annotations

import time

import faiss
import numpy as np
from sklearn.decomposition import TruncatedSVD

from .data import Dataset


def seen_mask(ds: Dataset) -> np.ndarray:
    m = np.zeros((ds.n_users, ds.n_items), dtype=bool)
    for u, seq in enumerate(ds.train):
        m[u, seq] = True
    return m


def metrics(
    ds: Dataset, scores: np.ndarray, ks=(10, 50), subset_items: set[int] | None = None
) -> dict:
    """Recall@K (hits / min(K, #relevant)), NDCG@10, mean popularity rank of the top-10, catalogue coverage of top-10 lists."""
    scores = np.where(seen_mask(ds), -np.inf, scores)
    top = np.argsort(-scores, axis=1)[:, : max(ks)]
    rec = {k: [] for k in ks}
    ndcg, pop_rank = [], []
    rank_of = np.argsort(np.argsort(-ds.pop))
    disc = 1 / np.log2(np.arange(2, 12))
    for u in range(ds.n_users):
        rel = {i for i in ds.test[u] if subset_items is None or i in subset_items}
        if not rel:
            continue
        for k in ks:
            rec[k].append(len(rel & set(top[u, :k].tolist())) / min(k, len(rel)))
        gains = np.array([1.0 if i in rel else 0.0 for i in top[u, :10]])
        ideal = disc[: min(len(rel), 10)].sum()
        ndcg.append(float((gains * disc).sum() / ideal))
        pop_rank.append(float(rank_of[top[u, :10]].mean()) / ds.n_items)
    return {**{f"recall@{k}": round(float(np.mean(v)), 4) for k, v in rec.items()}, "ndcg@10": round(float(np.mean(ndcg)), 4), "mean_pop_rank_top10": round(float(np.mean(pop_rank)), 3),
            "coverage@10": round(len(set(top[:, :10].ravel().tolist())) / ds.n_items, 3), "users": len(ndcg)}  # fmt: skip


def popularity_scores(ds: Dataset) -> np.ndarray:
    return np.tile(ds.pop, (ds.n_users, 1))


def interaction_matrix(ds: Dataset) -> np.ndarray:
    m = np.zeros((ds.n_users, ds.n_items), dtype=np.float32)
    for u, seq in enumerate(ds.train):
        m[u, seq] = 1
    return m


def cooccurrence_scores(ds: Dataset) -> np.ndarray:
    m = interaction_matrix(ds)
    co = m.T @ m
    norm = np.sqrt(np.diag(co)) + 1e-9
    co = co / norm[:, None] / norm[None, :]
    np.fill_diagonal(co, 0)
    return m @ co


def svd_scores(ds: Dataset, k: int = 32, seed: int = 0) -> np.ndarray:
    m = interaction_matrix(ds)
    svd = TruncatedSVD(k, random_state=seed)
    return svd.fit_transform(m) @ svd.components_


def tower_scores(model, ds: Dataset, history: int = 10) -> np.ndarray:
    users = np.arange(ds.n_users)
    uv = model.user_vectors(users, [seq[-history:] for seq in ds.train])
    return uv @ model.all_item_vectors().T


def ann_report(item_vecs: np.ndarray, user_vecs: np.ndarray, k: int = 50, seed: int = 0) -> dict:
    """Recall of approximate indexes against exact inner-product search, with per-query latency."""
    d = item_vecs.shape[1]
    exact = faiss.IndexFlatIP(d)
    exact.add(item_vecs)
    t0 = time.perf_counter()
    _, gt = exact.search(user_vecs, k)
    t_exact = (time.perf_counter() - t0) / len(user_vecs) * 1000
    out = {"flat_exact": {"recall_vs_exact": 1.0, "ms_per_query": round(t_exact, 4)}}
    hnsw = faiss.IndexHNSWFlat(d, 16, faiss.METRIC_INNER_PRODUCT)
    hnsw.hnsw.efConstruction = 80
    hnsw.add(item_vecs)
    ivf = faiss.IndexIVFFlat(faiss.IndexFlatIP(d), d, 16, faiss.METRIC_INNER_PRODUCT)
    ivf.train(item_vecs)
    ivf.add(item_vecs)
    for name, idx, setter in (
        ("hnsw_ef16", hnsw, lambda: setattr(hnsw.hnsw, "efSearch", 16)),
        ("hnsw_ef64", hnsw, lambda: setattr(hnsw.hnsw, "efSearch", 64)),
        ("ivf_nprobe1", ivf, lambda: setattr(ivf, "nprobe", 1)),
        ("ivf_nprobe4", ivf, lambda: setattr(ivf, "nprobe", 4)),
    ):
        setter()
        t0 = time.perf_counter()
        _, got = idx.search(user_vecs, k)
        ms = (time.perf_counter() - t0) / len(user_vecs) * 1000
        rec = float(np.mean([len(set(got[i]) & set(gt[i])) / k for i in range(len(gt))]))
        out[name] = {"recall_vs_exact": round(rec, 4), "ms_per_query": round(ms, 4)}
    return out
