"""POC 092: two-tower retrieval (NumPy, hand-written backprop) vs popularity / co-occurrence / SVD, plus FAISS ANN recall."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from pocs_common import write_json
from pocs_common.report import markdown_table

from .data import generate, validation_split
from .evaluate import (
    ann_report,
    cooccurrence_scores,
    metrics,
    popularity_scores,
    svd_scores,
    tower_scores,
)
from .towers import TwoTower, train

POC_DIR = Path(__file__).resolve().parent.parent


def select_epochs(ds, max_epochs: int = 8, seed: int = 0) -> tuple[int, dict[int, float]]:
    """Train on a validation split (each user's last training item held out) and keep the epoch count with the best validation recall@50."""
    val = validation_split(ds)
    model, curve = TwoTower(val, seed=seed), {}

    def log(ep, _loss):
        curve[ep + 1] = metrics(val, tower_scores(model, val))["recall@50"]

    train(model, epochs=max_epochs, seed=seed, log=log)
    return max(curve, key=lambda e: (curve[e], -e)), curve


def run(epochs: int | None = None, seed: int = 0) -> dict:
    ds = generate(seed=seed)
    curve: dict[int, float] = {}
    if epochs is None:
        epochs, curve = select_epochs(ds, seed=seed)
    model = TwoTower(ds, seed=seed)
    losses = train(model, epochs=epochs, seed=seed)
    cold = ds.cold_items
    res: dict = {
        "users": ds.n_users,
        "items": ds.n_items,
        "train_interactions": sum(len(s) for s in ds.train),
        "epochs_used": epochs,
        "validation_recall50_by_epoch": curve,
        "loss_first_last": [round(losses[0], 3), round(losses[-1], 3)],
        "models": {},
    }
    for name, sc in (
        ("popularity", popularity_scores(ds)),
        ("item-item co-occurrence", cooccurrence_scores(ds)),
        ("SVD (32 factors)", svd_scores(ds)),
        ("two-tower", tower_scores(model, ds)),
    ):
        res["models"][name] = {
            "all": metrics(ds, sc),
            "cold_items_only": metrics(ds, sc, ks=(10, 50), subset_items=cold)
            if any(i in cold for t in ds.test for i in t)
            else None,
        }
    users = list(range(ds.n_users))
    uv = model.user_vectors(np.array(users), [s[-10:] for s in ds.train])
    res["ann"] = ann_report(model.all_item_vectors(), uv)
    return res


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    rows = [
        [
            n,
            m["all"]["recall@10"],
            m["all"]["recall@50"],
            m["all"]["ndcg@10"],
            m["all"]["mean_pop_rank_top10"],
            m["all"]["coverage@10"],
            (m["cold_items_only"] or {}).get("recall@50", "-"),
        ]
        for n, m in r["models"].items()
    ]
    md = ["# Results (measured by `python -m two_tower_recommender.main`; synthetic data, temporal leave-last-2-out, NumPy two-tower, FAISS 1.13)", "", f"{r['users']} users, {r['items']} items, {r['train_interactions']} training interactions; two-tower training loss {r['loss_first_last'][0]} -> {r['loss_first_last'][1]} over {r['epochs_used']} epochs (chosen on a validation split; validation recall@50 by epoch: {r['validation_recall50_by_epoch']}).", "",
          markdown_table(["model", "recall@10", "recall@50", "NDCG@10", "mean popularity rank of top-10 (0=most popular)", "coverage@10", "recall@50 on cold items"], rows), "", "## ANN retrieval (k=50) vs exact inner-product search", "",
          markdown_table(["index", "recall vs exact", "ms / query"], [[k, v["recall_vs_exact"], v["ms_per_query"]] for k, v in r["ann"].items()])]  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
