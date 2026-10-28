"""Detector -> Triage -> Fixer -> Verifier -> Gate. Each agent is a pure function over a shared state.

The agents here are deterministic rule-based roles, not LLMs. An LLM could replace the Triage
rationale or Fixer proposal step, but its output would have to pass the same allow-listed operation
schema and Verifier, so the safety properties do not depend on the model.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

import pandas as pd

from .checks import STATUS_SET, Issue, run_checks

ALLOWED_OPS = {"map_values", "drop_duplicates", "quarantine"}
AUTO_CONF, AUTO_MAX_FRACTION = 0.9, 0.05
CRITICAL = {"order_id": 3, "customer_id": 3, "amount": 3, "status": 2, "order_date": 2}


@dataclass
class State:
    orders: pd.DataFrame
    customers: pd.DataFrame
    trace: list[dict] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    triage: list[dict] = field(default_factory=list)
    proposals: list[dict] = field(default_factory=list)
    applied: list[dict] = field(default_factory=list)
    queued: list[dict] = field(default_factory=list)
    quarantine: pd.DataFrame = None  # rows removed from the clean output, kept for review
    clean: pd.DataFrame = None

    def log(self, agent: str, **kw):
        self.trace.append({"agent": agent, **kw})


def detector(s: State) -> State:
    s.issues = run_checks(s.orders, s.customers)
    s.log("detector", issues=[(i.check, i.column, i.n) for i in s.issues])
    return s


def triage(s: State) -> State:
    n = len(s.orders)
    for i in s.issues:
        frac = i.n / n
        sev = (
            "high"
            if frac > 0.02 or CRITICAL.get(i.column, 1) == 3 and i.check in {"unique", "fk_exists"}
            else "medium"
            if frac > 0.005
            else "low"
        )
        category = {"in_set": "auto_fixable", "unique": "auto_fixable"}.get(i.check, "needs_human")
        s.triage.append({"check": i.check, "column": i.column, "n": i.n, "severity": sev, "category": category,
                         "owner": "ingestion-team" if i.check in {"unique", "fk_exists"} else "source-system-owner" if i.check in {"range_min", "not_future", "not_null"} else "data-steward"})  # fmt: skip
    s.log("triage", decisions=[(t["check"], t["severity"], t["category"]) for t in s.triage])
    return s


def _closest(value: str) -> tuple[str | None, float]:
    v = str(value).strip().lower()
    m = difflib.get_close_matches(v, sorted(STATUS_SET), n=1, cutoff=0.7)
    return (m[0], round(difflib.SequenceMatcher(None, v, m[0]).ratio(), 2)) if m else (None, 0.0)


def fixer(s: State) -> State:
    for t in s.triage:
        issue = next(i for i in s.issues if (i.check, i.column) == (t["check"], t["column"]))
        if t["check"] == "in_set":
            bad = s.orders.loc[s.orders.order_id.isin(issue.rows), "status"].unique()
            mapping, conf = {}, 1.0
            for b in bad:
                tgt, c = _closest(b)
                if tgt:
                    mapping[b] = tgt
                    conf = min(conf, c)
            if mapping:
                s.proposals.append(
                    {
                        "op": "map_values",
                        "column": "status",
                        "mapping": mapping,
                        "confidence": round(0.85 + 0.15 * conf, 2),
                        "issue": (t["check"], t["column"]),
                        "rows": issue.n,
                        "reason": "case/whitespace/near-miss spelling of an allowed status",
                    }
                )
        elif t["check"] == "unique":
            s.proposals.append(
                {
                    "op": "drop_duplicates",
                    "key": "order_id",
                    "keep": "first",
                    "confidence": 0.95,
                    "issue": (t["check"], t["column"]),
                    "rows": issue.n,
                    "reason": "exact repeated rows",
                }
            )
        else:  # guessing a customer, an amount or a date would be inventing data: quarantine instead
            s.proposals.append(
                {
                    "op": "quarantine",
                    "check": t["check"],
                    "column": t["column"],
                    "confidence": 0.99,
                    "issue": (t["check"], t["column"]),
                    "rows": issue.n,
                    "reason": "cannot be repaired without the source of truth",
                }
            )
    s.log("fixer", proposals=[(p["op"], p["issue"], p["confidence"]) for p in s.proposals])
    return s


def apply_op(
    df: pd.DataFrame, op: dict, customers: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply one allow-listed operation; return (new frame, quarantined rows). No free-form code paths."""
    if op["op"] not in ALLOWED_OPS:
        raise ValueError(f"operation {op.get('op')!r} is not allowed")
    empty = df.iloc[0:0]
    if op["op"] == "map_values":
        if op["column"] != "status":
            raise ValueError("map_values is only allowed on status")
        out = df.copy()
        out["status"] = out["status"].map(lambda v: op["mapping"].get(v, v))
        return out, empty
    if op["op"] == "drop_duplicates":
        return df.drop_duplicates(subset=[op["key"]], keep=op["keep"]), empty
    from .checks import run_checks as rc

    bad = {i for i in rc(df, customers) if (i.check, i.column) == (op["check"], op["column"])}
    ids = set().union(*[set(i.rows) for i in bad]) if bad else set()
    mask = df.order_id.isin(ids)
    return df[~mask], df[mask]


def verifier_and_gate(s: State) -> State:
    work = s.orders.copy()
    s.quarantine = s.orders.iloc[0:0]
    order = {"drop_duplicates": 0, "map_values": 1, "quarantine": 2}
    for p in sorted(s.proposals, key=lambda p: order[p["op"]]):
        trial, q = apply_op(work, p, s.customers)
        before, after = (
            {(i.check, i.column) for i in run_checks(work, s.customers)},
            {(i.check, i.column) for i in run_checks(trial, s.customers)},
        )
        resolved = p["issue"] not in after
        no_new = after <= before
        frac = (len(work) - len(trial)) / len(work) if len(work) else 0
        touched = [
            c
            for c in work.columns
            if c in trial.columns and len(trial) == len(work) and not work[c].equals(trial[c])
        ]
        scope_ok = p["op"] != "map_values" or touched in ([], ["status"])
        passed = resolved and no_new and scope_ok
        auto = (
            passed
            and p["confidence"] >= AUTO_CONF
            and frac <= AUTO_MAX_FRACTION
            and p["op"] != "quarantine"
        )
        rec = {
            **p,
            "verified": passed,
            "removed_fraction": round(frac, 4),
            "decision": "auto_applied" if auto else "queued_for_human" if passed else "rejected",
        }
        if auto:
            work = trial
            s.applied.append(rec)
        else:  # includes every quarantine: a human approves removing rows; until then they stay, flagged
            s.queued.append(rec)
    s.clean = work
    s.log(
        "verifier_gate",
        decisions=[(r["op"], r["issue"], r["decision"]) for r in s.applied + s.queued],
    )
    return s


def run_pipeline(orders: pd.DataFrame, customers: pd.DataFrame) -> State:
    s = State(orders.copy(), customers)
    for step in (detector, triage, fixer, verifier_and_gate):
        s = step(s)
    return s
