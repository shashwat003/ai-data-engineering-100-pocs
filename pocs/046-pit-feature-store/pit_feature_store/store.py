"""Minimal feature store: registry, offline point-in-time join (DuckDB ASOF), online key-value store.

Point-in-time correctness: for each (entity, event_ts) row the feature value is the latest one with
`feature_ts <= event_ts` (and not older than the view's TTL). `leaky_join` takes the latest value
overall and exists only to show what goes wrong without PIT semantics.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")


class FeatureStoreError(ValueError):
    pass


@dataclass(frozen=True)
class FeatureView:
    name: str
    entity: str
    ts_col: str
    features: tuple[str, ...]
    ttl: timedelta

    def __post_init__(self):
        for x in (self.name, self.entity, self.ts_col, *self.features):
            if not IDENT.match(x):
                raise FeatureStoreError(f"unsafe identifier {x!r}")
        if self.ttl <= timedelta(0):
            raise FeatureStoreError("ttl must be positive")


class OfflineStore:
    def __init__(self):
        self.con = duckdb.connect()
        self.views: dict[str, FeatureView] = {}

    def register(self, view: FeatureView, df: pd.DataFrame) -> None:
        missing = {view.entity, view.ts_col, *view.features} - set(df.columns)
        if missing:
            raise FeatureStoreError(f"source for {view.name} lacks columns {sorted(missing)}")
        if df[view.entity].isna().any() or df[view.ts_col].isna().any():
            raise FeatureStoreError(f"{view.name}: null entity or timestamp in source")
        if df.duplicated([view.entity, view.ts_col]).any():
            raise FeatureStoreError(
                f"{view.name}: duplicate (entity, timestamp) rows are ambiguous"
            )
        self.views[view.name] = view
        src = df[[view.entity, view.ts_col, *view.features]].copy()
        src[view.ts_col] = pd.to_datetime(src[view.ts_col])
        self.con.register(f"src_{view.name}", src)

    def historical_features(
        self, entity_df: pd.DataFrame, view_names: list[str], *, leaky: bool = False
    ) -> pd.DataFrame:
        if not {"entity_id", "event_ts"} <= set(entity_df.columns):
            raise FeatureStoreError("entity_df needs entity_id and event_ts")
        out = entity_df.copy()
        out["event_ts"] = pd.to_datetime(out["event_ts"])
        out["_row"] = range(len(out))
        for name in view_names:
            v = self.views.get(name) or (_ for _ in ()).throw(
                FeatureStoreError(f"unknown view {name}")
            )
            self.con.register("ent", out[["_row", "entity_id", "event_ts"]])
            cols = ", ".join(f's."{f}" as "{f}"' for f in v.features)
            if leaky:
                src = f'(select * from src_{v.name} qualify row_number() over (partition by "{v.entity}" order by "{v.ts_col}" desc) = 1)'  # noqa: S608
                join = f'left join {src} s on e.entity_id = s."{v.entity}"'
            else:
                join = f'asof left join src_{v.name} s on e.entity_id = s."{v.entity}" and e.event_ts >= s."{v.ts_col}"'
            f = self.con.execute(
                f'select e._row, s."{v.ts_col}" as _fts, {cols} from ent e {join}'
            ).df()  # noqa: S608
            if not leaky:
                age = (
                    out.set_index("_row")["event_ts"].reindex(f["_row"]).to_numpy()
                    - f["_fts"].to_numpy()
                )
                f.loc[age > pd.Timedelta(v.ttl), list(v.features)] = None
            out = out.merge(f.drop(columns="_fts").drop_duplicates("_row"), on="_row", how="left")
        return out.drop(columns="_row")


class OnlineStore:
    """Latest feature values per entity, persisted as JSON (stand-in for Redis)."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.data: dict[str, dict] = {}
        if self.path and self.path.exists():
            self.data = json.loads(self.path.read_text())

    def materialize(self, offline: OfflineStore, view_name: str, as_of: datetime) -> int:
        v = offline.views[view_name]
        cols = ", ".join(f'"{f}"' for f in v.features)
        rows = offline.con.execute(
            f"""select "{v.entity}", "{v.ts_col}", {cols} from src_{v.name} where "{v.ts_col}" <= ?
                qualify row_number() over (partition by "{v.entity}" order by "{v.ts_col}" desc) = 1""",  # noqa: S608
            [as_of],
        ).fetchall()
        for r in rows:
            self.data.setdefault(f"{view_name}:{r[0]}", {}).update(
                {"ts": r[1].isoformat(), **dict(zip(v.features, r[2:], strict=True))}
            )
        if self.path:
            self.path.write_text(json.dumps(self.data))
        return len(rows)

    def get(self, views: dict[str, FeatureView], view_name: str, entity_id, now: datetime) -> dict:
        v = views[view_name]
        rec = self.data.get(f"{view_name}:{entity_id}")
        if rec is None or now - datetime.fromisoformat(rec["ts"]) > v.ttl:
            return {f: None for f in v.features}
        return {f: rec[f] for f in v.features}
