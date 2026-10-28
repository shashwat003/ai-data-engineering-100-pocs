"""POC 022: Drain template mining, anomaly rules and guarded explanations of flagged clusters."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .detect import detect, mine, prf
from .explain import SimSLM, explain
from .logs import INJECTION, generate

POC_DIR = Path(__file__).resolve().parent.parent


def run(seed: int = 5) -> dict:
    g = generate(seed=seed)
    miner, assigned = mine(g["lines"])
    flags = detect(assigned, g["train_minutes"])
    metrics = prf(flags, g["truth_minutes"], g["train_minutes"], g["minutes"])
    templates = {c.cluster_id: c.get_template() for c in miner.drain.clusters}
    # explain each distinct (cluster, reason kind) once, using its first flagged minute
    seen, explanations = set(), []
    base = Counter()
    for m, _s, c in assigned:
        if m < g["train_minutes"]:
            base[c] += 1 / g["train_minutes"]
    for m in sorted(flags):
        for r in flags[m]:
            kind, cid = r.split(":")[0], int(r.split(":")[1])
            if (kind, cid) in seen:
                continue
            seen.add((kind, cid))
            ex = [
                ln.text
                for ln in g["lines"]
                if ln.minute == m
                and (ln.service + " " + ln.text).startswith(
                    templates[cid].split("<")[0].strip()[:12]
                )
            ][:3]
            count = sum(1 for mm, _s, c in assigned if mm == m and c == cid)
            e = explain(
                SimSLM(), cid, templates[cid], r, ex or [templates[cid]], count, base.get(cid, 0.0)
            )
            explanations.append({"minute": m, "reason": r, "template": templates[cid], **e})
    # hostile-log tests: a gullible model must be caught by validation, a broken one must fall back
    inj_ex = explain(
        SimSLM(gullible=True),
        999,
        "api error: disk quota exceeded; <*>",
        "new_template:999",
        [f"error: disk quota exceeded; {INJECTION}"],
        1,
        0.0,
    )
    broken_ex = explain(SimSLM(broken=True), 998, "x", "spike:998", ["x"], 5, 0.1)
    return {"n_lines": len(g["lines"]), "n_templates": len(templates), "metrics": metrics, "flagged_minutes": len(flags), "incidents": g["incidents"],
            "explanations": explanations, "gullible_model_source": inj_ex["source"], "broken_model_source": broken_ex["source"]}  # fmt: skip


def main() -> None:
    res = run()
    write_json(POC_DIR / "results" / "results.json", res)
    m = res["metrics"]
    rows = [
        (e["minute"], e["reason"], e["severity"], e["source"], e["likely_cause"])
        for e in res["explanations"]
    ]
    md = (
        f"# Results: log anomaly detection and guarded explanation ({res['n_lines']} synthetic log lines, {res['n_templates']} Drain templates)\n\n"
        "Drain3 (real) mines templates; rule-based detectors flag new templates, rate spikes and lost heartbeats against a 60-minute baseline. "
        "The explainer is a SIMULATED small model (keyword rules); no Ollama model was run.\n\n"
        f"Window-level detection: precision {m['precision']}, recall {m['recall']}, false-positive windows {m['false_positive_windows']}, missed windows {m['missed_windows']}.\n\n"
        + markdown_table(["first minute", "reason", "severity", "source", "likely cause"], rows)
        + f"\n\nHostile log line (prompt injection) against a deliberately gullible model: reply accepted from model = {res['gullible_model_source'] == 'model'} (source: {res['gullible_model_source']}). "
        f"Malformed model output: source = {res['broken_model_source']}.\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
