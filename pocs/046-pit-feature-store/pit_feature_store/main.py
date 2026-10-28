"""POC 046: point-in-time joins stop label leakage; online store matches offline values at serving time."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

from pocs_common import write_json
from pocs_common.report import markdown_table

from .generate import START, generate
from .store import FeatureView, OfflineStore, OnlineStore

POC_DIR = Path(__file__).resolve().parent.parent
VIEW = FeatureView(
    "cust", "customer_id", "feature_ts", ("orders_30d", "tickets_7d"), timedelta(days=3)
)
X = ["orders_30d", "tickets_7d"]


def run() -> dict:
    feats, labels = generate()
    off = OfflineStore()
    off.register(VIEW, feats)
    train, test = labels.iloc[:400], labels.iloc[400:]
    res: dict = {"n_train": len(train), "n_test": len(test)}
    # honest evaluation = features as they were known at event time (what serving would see)
    test_pit = off.historical_features(test, ["cust"]).dropna(subset=X)
    for name, leaky in (("leaky_join", True), ("point_in_time", False)):
        tr = off.historical_features(train, ["cust"], leaky=leaky).dropna(subset=X)
        te_same = off.historical_features(test, ["cust"], leaky=leaky).dropna(subset=X)
        m = LogisticRegression(max_iter=1000).fit(tr[X], tr["churned"])
        res[name] = {
            "auc_offline_same_join": round(
                float(roc_auc_score(te_same["churned"], m.predict_proba(te_same[X])[:, 1])), 3
            ),
            "auc_at_serving_time": round(
                float(roc_auc_score(test_pit["churned"], m.predict_proba(test_pit[X])[:, 1])), 3
            ),
        }
    # online/offline parity at a materialisation instant
    now = START + timedelta(days=60, hours=3)
    online = OnlineStore()
    n = online.materialize(off, "cust", now)
    sample = labels.iloc[:50].assign(event_ts=now)
    pit = off.historical_features(sample, ["cust"]).set_index("entity_id")
    mism = sum(
        online.get({"cust": VIEW}, "cust", e, now) != {f: pit.loc[e, f] for f in VIEW.features}
        for e in pit.index
    )
    res["online_materialised_entities"] = n
    res["parity_mismatches_of_50"] = int(mism)
    res["online_stale_returns_none"] = online.get(
        {"cust": VIEW}, "cust", 0, now + timedelta(days=10)
    ) == {f: None for f in VIEW.features}
    res["online_unknown_entity_none"] = (
        online.get({"cust": VIEW}, "cust", 99999, now)["orders_30d"] is None
    )
    return res


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    md = ["# Results (measured by `python -m pit_feature_store.main`)", "", f"Train {r['n_train']} / test {r['n_test']} customers. AUC of a logistic regression on [orders_30d, tickets_7d].", "",
          markdown_table(["training join", "AUC (test, same join: looks great)", "AUC at serving time (PIT features)"], [[k, r[k]["auc_offline_same_join"], r[k]["auc_at_serving_time"]] for k in ("leaky_join", "point_in_time")]), "",
          f"- online store materialised {r['online_materialised_entities']} entities; parity mismatches vs offline PIT join: {r['parity_mismatches_of_50']}/50",
          f"- stale online record returns None: {r['online_stale_returns_none']}; unknown entity returns None: {r['online_unknown_entity_none']}"]  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
