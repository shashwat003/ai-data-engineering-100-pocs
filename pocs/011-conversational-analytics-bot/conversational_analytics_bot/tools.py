"""Validated tool calls -> parameterised SQL -> governed result. Every call is audited."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .semantic import (
    COMPLETED,
    DIMENSIONS,
    FROM,
    MAX_ROWS,
    METRICS,
    MIN_GROUP,
    ROLES,
)

ALLOWED_TOOLS = {"query_metric", "describe_metrics"}
VALUE_RE = re.compile(r"^[A-Za-z0-9 _\-\.]{1,40}$")  # filter values are data, but still bounded
MONTH_RE = re.compile(r"^\d{4}(-\d{2})?$")


class Refusal(Exception):
    """Request outside the governed surface; the message is safe to show the user."""


@dataclass
class User:
    name: str
    role: str
    country: str | None = None


@dataclass
class Answer:
    ok: bool
    message: str
    rows: list[tuple] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    suppressed: int = 0
    sql: str = ""


def validate(call: dict) -> dict:
    if not isinstance(call, dict) or call.get("tool") not in ALLOWED_TOOLS:
        raise Refusal(
            f"tool {call.get('tool') if isinstance(call, dict) else call!r} is not available"
        )
    if call["tool"] == "describe_metrics":
        return {"tool": "describe_metrics"}
    metric = call.get("metric")
    if metric not in METRICS:
        raise Refusal(f"unknown metric {metric!r}; available: {sorted(METRICS)}")
    group_by = call.get("group_by") or []
    if not isinstance(group_by, list) or len(group_by) > 2:
        raise Refusal("group_by must be a list of at most 2 dimensions")
    for d in group_by:
        if d not in DIMENSIONS:
            raise Refusal(f"unknown dimension {d!r}; available: {sorted(DIMENSIONS)}")
    filters = call.get("filters") or []
    clean = []
    for f in filters if isinstance(filters, list) else []:
        dim, op, val = f.get("dim"), f.get("op", "eq"), f.get("value")
        if dim not in DIMENSIONS or op not in {"eq", "in"}:
            raise Refusal(f"filter on {dim!r} with op {op!r} is not allowed")
        vals = val if isinstance(val, list) else [val]
        if (
            not vals
            or len(vals) > 10
            or not all(isinstance(v, str) and VALUE_RE.match(v) for v in vals)
        ):
            raise Refusal("filter values must be short plain strings")
        if dim in {"month", "year"} and not all(MONTH_RE.match(v) for v in vals):
            raise Refusal("date filters must look like 2024 or 2024-03")
        clean.append({"dim": dim, "op": "in" if len(vals) > 1 else op, "value": vals})
    limit = (
        min(int(call.get("limit", MAX_ROWS)), MAX_ROWS)
        if str(call.get("limit", "0")).lstrip("-").isdigit()
        else MAX_ROWS
    )
    return {
        "tool": "query_metric",
        "metric": metric,
        "group_by": group_by,
        "filters": clean,
        "limit": max(1, limit),
    }


def compile_sql(call: dict, user: User) -> tuple[str, list]:
    params: list = []
    where = [COMPLETED]
    for f in call["filters"]:
        col = DIMENSIONS[f["dim"]]
        if f["dim"] in {"month", "year"}:
            where.append("(" + " OR ".join(f"{col} = ?" for _ in f["value"]) + ")")
        else:
            where.append(f"{col} IN ({', '.join('?' for _ in f['value'])})")
        params += f["value"]
    if ROLES[user.role].get(
        "country"
    ):  # row-level security: appended AFTER user filters, cannot be removed
        if not user.country:
            raise Refusal("this role requires a country assignment")
        where.append("c.country = ?")
        params.append(user.country)
    dims = [DIMENSIONS[d] for d in call["group_by"]]
    select = ", ".join([*(f"{d} AS {n}" for d, n in zip(dims, call["group_by"], strict=True)), f"{METRICS[call['metric']]['sql']} AS {call['metric']}", "COUNT(DISTINCT c.customer_id) AS _n"])  # fmt: skip
    sql = f"SELECT {select} {FROM} WHERE {' AND '.join(where)}"
    if dims:
        sql += f" GROUP BY {', '.join(dims)} ORDER BY {call['metric']} DESC"
    sql += f" LIMIT {int(call['limit'])}"
    return sql, params


def run_tool(con, call: dict, user: User, audit: list) -> Answer:
    try:
        v = validate(call)
        if v["tool"] == "describe_metrics":
            msg = "; ".join(f"{k}: {m['desc']}" for k, m in METRICS.items())
            audit.append({"user": user.name, "call": v, "outcome": "ok"})
            return Answer(True, msg)
        sql, params = compile_sql(v, user)
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except Refusal as e:
        audit.append(
            {
                "user": user.name,
                "call": call if isinstance(call, dict) else str(call),
                "outcome": f"refused: {e}",
            }
        )
        return Answer(False, f"I can't do that: {e}")
    n_idx = cols.index("_n")
    kept = [r for r in rows if r[n_idx] >= MIN_GROUP] if v["group_by"] else rows
    suppressed = len(rows) - len(kept)
    if not v["group_by"] and rows and rows[0][n_idx] < MIN_GROUP:
        kept, suppressed = [], 1
    out_cols = [c for c in cols if c != "_n"]
    out = [tuple(x for i, x in enumerate(r) if i != n_idx) for r in kept]
    audit.append(
        {
            "user": user.name,
            "call": v,
            "sql": sql,
            "outcome": f"ok rows={len(out)} suppressed={suppressed}",
        }
    )
    return Answer(
        True,
        f"{len(out)} row(s)" + (f"; {suppressed} small group(s) suppressed" if suppressed else ""),
        out,
        out_cols,
        suppressed,
        sql,
    )
