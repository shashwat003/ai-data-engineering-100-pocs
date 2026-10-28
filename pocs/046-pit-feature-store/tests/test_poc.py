from datetime import datetime, timedelta

import pandas as pd
import pytest
from pit_feature_store.main import VIEW, run
from pit_feature_store.store import FeatureStoreError, FeatureView, OfflineStore, OnlineStore

T = datetime(2026, 1, 1)


def store(rows):
    o = OfflineStore()
    o.register(
        VIEW, pd.DataFrame(rows, columns=["customer_id", "feature_ts", "orders_30d", "tickets_7d"])
    )
    return o


def ask(o, *pairs, leaky=False):
    df = pd.DataFrame({"entity_id": [p[0] for p in pairs], "event_ts": [p[1] for p in pairs]})
    return o.historical_features(df, ["cust"], leaky=leaky)


def test_picks_latest_value_at_or_before_event_never_future():
    o = store([(1, T, 1, 0), (1, T + timedelta(days=1), 2, 0), (1, T + timedelta(days=2), 99, 0)])
    out = ask(o, (1, T + timedelta(days=1, hours=5)), (1, T + timedelta(days=1)))
    assert out["orders_30d"].tolist() == [
        2,
        2,
    ]  # boundary inclusive, the day-2 value (99) is in the future
    assert ask(o, (1, T + timedelta(days=1, hours=5)), leaky=True)["orders_30d"].tolist() == [99]


def test_before_first_feature_and_unknown_entity_are_null():
    o = store([(1, T + timedelta(days=1), 5, 0)])
    out = ask(o, (1, T), (2, T + timedelta(days=5)))
    assert out["orders_30d"].isna().all()


def test_ttl_expires_old_values():
    o = store([(1, T, 5, 1)])
    out = ask(o, (1, T + timedelta(days=2)), (1, T + timedelta(days=4)))
    assert out["orders_30d"].iloc[0] == 5 and pd.isna(out["orders_30d"].iloc[1])


def test_row_order_and_extra_columns_preserved():
    o = store([(1, T, 1, 0), (2, T, 2, 0)])
    df = pd.DataFrame(
        {"entity_id": [2, 1, 2], "event_ts": [T + timedelta(hours=1)] * 3, "label": [7, 8, 9]}
    )
    out = o.historical_features(df, ["cust"])
    assert out["label"].tolist() == [7, 8, 9] and out["orders_30d"].tolist() == [2, 1, 2]


def test_input_validation():
    with pytest.raises(FeatureStoreError):
        FeatureView("v", "e", "ts", ("a; drop table x",), timedelta(days=1))
    with pytest.raises(FeatureStoreError):
        FeatureView("v", "e", "ts", ("a",), timedelta(0))
    with pytest.raises(FeatureStoreError):
        store([(1, T, 1, 0), (1, T, 2, 0)])  # ambiguous duplicate
    with pytest.raises(FeatureStoreError):
        store([(None, T, 1, 0)])
    o = OfflineStore()
    with pytest.raises(FeatureStoreError):
        o.register(VIEW, pd.DataFrame({"customer_id": [1]}))
    with pytest.raises(FeatureStoreError):
        o.historical_features(pd.DataFrame({"x": [1]}), ["cust"])
    with pytest.raises(FeatureStoreError):
        store([(1, T, 1, 0)]).historical_features(
            pd.DataFrame({"entity_id": [1], "event_ts": [T]}), ["nope"]
        )


def test_online_materialisation_is_point_in_time_and_persists(tmp_path):
    o = store([(1, T, 1, 0), (1, T + timedelta(days=1), 2, 0), (1, T + timedelta(days=5), 50, 0)])
    on = OnlineStore(tmp_path / "online.json")
    assert on.materialize(o, "cust", T + timedelta(days=2)) == 1
    now = T + timedelta(days=2)
    assert on.get({"cust": VIEW}, "cust", 1, now)["orders_30d"] == 2  # not the day-5 value
    assert (
        OnlineStore(tmp_path / "online.json").get({"cust": VIEW}, "cust", 1, now)["orders_30d"] == 2
    )
    assert on.get({"cust": VIEW}, "cust", 1, now + timedelta(days=9))["orders_30d"] is None


def test_demo_shows_leakage_and_parity():
    r = run()
    assert r["leaky_join"]["auc_offline_same_join"] > 0.9
    assert r["leaky_join"]["auc_at_serving_time"] < r["leaky_join"]["auc_offline_same_join"] - 0.2
    assert (
        abs(r["point_in_time"]["auc_offline_same_join"] - r["point_in_time"]["auc_at_serving_time"])
        < 1e-9
    )
    assert (
        r["parity_mismatches_of_50"] == 0
        and r["online_stale_returns_none"]
        and r["online_unknown_entity_none"]
    )
