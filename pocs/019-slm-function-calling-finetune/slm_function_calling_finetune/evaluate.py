"""Policies and metrics for tool calling."""

from __future__ import annotations

import json

from .model import Tagger, assemble, baseline_route, baseline_spans, route
from .tools import parse_tool_call


def decide(policy: str, ex: dict, router, tagger: Tagger | None, tau: float = 0.6) -> dict:
    """Return {'kind': 'call'|'none'|'clarify', 'call': dict|None, 'text': str|None} for one utterance."""
    text = ex["text"]
    if policy == "baseline":
        tool, conf = baseline_route(text)
        if tool == "none":
            return {"kind": "none", "call": None}
        call = assemble(tool, baseline_spans(tool, text))
        return {"kind": "call", "call": call, "text": json.dumps(call)}
    tools, conf = route(router, [text])
    tool, c = tools[0], float(conf[0])
    if tool == "none":
        return {"kind": "none", "call": None}
    call = assemble(tool, tagger.spans(text))
    raw = json.dumps(call)
    if policy == "tuned":
        return {"kind": "call", "call": call, "text": raw}
    # tuned + gate: low confidence or schema-invalid -> ask the user instead of acting
    parsed, errs = parse_tool_call(raw)
    if c < tau or parsed is None:
        return {"kind": "clarify", "call": None, "errors": errs}
    return {"kind": "call", "call": parsed, "text": raw}


def outcome(ex: dict, d: dict) -> str:
    from .tools import validate_call

    gold = ex["gold"]
    if d["kind"] == "clarify":
        return "clarify"
    if gold is None:
        return "correct_abstain" if d["kind"] == "none" else "hallucinated_call"
    if d["kind"] == "none":
        return "missed_call"
    call = d["call"]
    if validate_call(call):
        return "invalid_call"
    return "correct_call" if call == gold else "wrong_but_valid_call"


def evaluate(policy: str, examples: list[dict], router, tagger, tau: float = 0.6) -> dict:
    counts: dict[str, int] = {}
    tool_ok = 0
    for ex in examples:
        d = decide(policy, ex, router, tagger, tau)
        o = outcome(ex, d)
        counts[o] = counts.get(o, 0) + 1
        pred_tool = d["call"]["name"] if d["call"] else ("none" if d["kind"] == "none" else None)
        tool_ok += pred_tool == ex["tool"]
    n = len(examples)
    emitted = sum(
        counts.get(k, 0)
        for k in ("correct_call", "wrong_but_valid_call", "invalid_call", "hallucinated_call")
    )
    gold_calls = sum(1 for e in examples if e["gold"])
    return {
        "n": n, "counts": counts,
        "tool_accuracy": round(tool_ok / n, 4),
        "exact_call_rate": round(counts.get("correct_call", 0) / max(1, gold_calls), 4),
        "invalid_call_rate": round(counts.get("invalid_call", 0) / max(1, emitted), 4),
        "wrong_but_valid_rate": round(counts.get("wrong_but_valid_call", 0) / max(1, emitted), 4),
        "hallucinated_calls": counts.get("hallucinated_call", 0),
        "clarify_rate": round(counts.get("clarify", 0) / n, 4),
    }  # fmt: skip
