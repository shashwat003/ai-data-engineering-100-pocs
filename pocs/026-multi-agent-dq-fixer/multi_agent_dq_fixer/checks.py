"""A small expectation engine (own implementation; Great Expectations not used)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

STATUS_SET = {"completed", "shipped", "cancelled", "returned"}
TODAY = pd.Timestamp("2025-01-01")


@dataclass(frozen=True)
class Issue:
    check: str
    column: str
    rows: tuple[int, ...]  # order_id values of offending rows (bounded in reports)

    @property
    def n(self) -> int:
        return len(self.rows)


def run_checks(orders: pd.DataFrame, customers: pd.DataFrame) -> list[Issue]:
    out: list[Issue] = []

    def add(check, col, mask, key="order_id"):
        ids = tuple(int(x) for x in orders.loc[mask, key])
        if ids:
            out.append(Issue(check, col, ids))

    add("not_null", "customer_id", orders.customer_id.isna())
    dup = orders.order_id.duplicated(keep=False)
    if dup.any():
        out.append(
            Issue("unique", "order_id", tuple(sorted({int(x) for x in orders.order_id[dup]})))
        )
    add("in_set", "status", ~orders.status.isin(STATUS_SET))
    add("range_min", "amount", orders.amount < 0)
    add("not_future", "order_date", pd.to_datetime(orders.order_date, errors="coerce") > TODAY)
    ok_fk = orders.customer_id.isna() | orders.customer_id.isin(customers.customer_id)
    add("fk_exists", "customer_id", ~ok_fk)
    return out
