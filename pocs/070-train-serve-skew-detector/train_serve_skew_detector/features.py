"""Synthetic events and two implementations of the same features: offline (training) and online (serving)."""

from __future__ import annotations

import numpy as np

DAY = 86_400
BUGS = (
    "unit_scaling",
    "window_off_by_one",
    "null_default",
    "category_case",
    "stale_cache",
    "future_leak",
)


def make_events(n_users: int = 300, days: int = 60, seed: int = 1) -> dict:
    rng = np.random.default_rng(seed)
    n = n_users * days // 3
    uid = rng.integers(0, n_users, n)
    ts = rng.integers(0, days * DAY, n)
    amount = np.round(rng.gamma(2.0, 30.0, n), 2)
    order = np.argsort(ts, kind="stable")
    return {"user": uid[order], "ts": ts[order], "amount": amount[order],
            "signup": rng.integers(-300 * DAY, 0, n_users), "country": rng.choice(np.array(["US", "DE", "IN", "BR"]), n_users),
            "premium": rng.random(n_users) < 0.3, "last_login": np.where(rng.random(n_users) < 0.15, np.nan, rng.uniform(0, 500, n_users)), "days": days}  # fmt: skip


def compute(
    ev: dict, queries: list[tuple[int, int]], mode: str, bugs: frozenset[str] = frozenset()
) -> dict[str, list]:
    """Feature values for (user, as_of_ts) queries. mode 'offline' = batch job; 'online' = serving path with `bugs`."""
    out = {
        k: []
        for k in (
            "avg_order_value_30d",
            "n_orders_7d",
            "days_since_signup",
            "country",
            "is_premium",
            "last_login_hours",
        )
    }
    for u, t in queries:
        m = ev["user"] == u
        ts, amt = ev["ts"][m], ev["amount"][m]
        horizon = t
        if mode == "offline" and "future_leak" in bugs:
            horizon = t + DAY  # the batch job accidentally included the following day
        in30 = (ts <= horizon) & (ts > t - 30 * DAY)
        lo7 = t - 7 * DAY - (DAY if (mode == "online" and "window_off_by_one" in bugs) else 0)
        in7 = (ts <= horizon) & (ts > lo7)
        avg = float(amt[in30].mean()) if in30.any() else np.nan
        if mode == "online" and "unit_scaling" in bugs and not np.isnan(avg):
            avg *= 100  # cents instead of dollars
        if mode == "online" and "stale_cache" in bugs and (u * 31 + t // DAY) % 5 == 0:
            prev = (ts <= t - DAY) & (ts > t - 31 * DAY)
            avg = float(amt[prev].mean()) if prev.any() else np.nan
        ll = ev["last_login"][u]
        if mode == "online" and "null_default" in bugs and np.isnan(ll):
            ll = 0.0
        country = str(ev["country"][u])
        if mode == "online" and "category_case" in bugs and u % 4 == 0:
            country = country.lower()
        out["avg_order_value_30d"].append(avg)
        out["n_orders_7d"].append(int(in7.sum()))
        out["days_since_signup"].append(int((t - ev["signup"][u]) // DAY))
        out["country"].append(country)
        out["is_premium"].append(bool(ev["premium"][u]))
        out["last_login_hours"].append(float(ll))
    return out
