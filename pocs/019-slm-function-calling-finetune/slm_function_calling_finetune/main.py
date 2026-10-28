"""POC 019: function-calling reliability on CPU. REAL: LoRA-adapted router (POC 015 code), logistic-regression slot tagger, typed
decoder, strict JSON-Schema gate, mutation tests. NOT USED: any language model, PEFT, GPU. The 'baseline' is a keyword/regex matcher."""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import numpy as np

from pocs_common import write_json
from pocs_common.report import markdown_table

from .data import generate
from .evaluate import evaluate
from .model import Tagger, pretrain_router, train_router
from .mutations import MALFORMED, mutate
from .tools import parse_tool_call, validate_call

POC_DIR = Path(__file__).resolve().parent.parent
METRICS = (
    "tool_accuracy",
    "exact_call_rate",
    "invalid_call_rate",
    "wrong_but_valid_rate",
    "hallucinated_calls",
    "clarify_rate",
)


def gate_audit(seed: int = 0, n: int = 600) -> dict:
    rng = random.Random(seed)
    valid = [e["gold"] for e in generate(n, "train", seed + 7) if e["gold"]]
    assert all(not validate_call(c) for c in valid)
    rejected, total, by_kind = 0, 0, {}
    for c in valid:
        m = mutate(c, rng)
        if m is None:
            continue
        kind, bad = m
        total += 1
        ok = parse_tool_call(json.dumps(bad))[0] is None
        rejected += ok
        by_kind.setdefault(kind, [0, 0])
        by_kind[kind][0] += ok
        by_kind[kind][1] += 1
    return {
        "valid_calls_accepted": sum(parse_tool_call(json.dumps(c))[0] is not None for c in valid), "valid_calls": len(valid),
        "mutations": total, "mutations_rejected": rejected, "by_kind": {k: f"{a}/{b}" for k, (a, b) in by_kind.items()},
        "malformed_outputs": len(MALFORMED), "malformed_rejected": sum(parse_tool_call(t)[0] is None for t in MALFORMED),
    }  # fmt: skip


def run(quick: bool = False) -> dict:
    seeds = range(2 if quick else 3)
    sizes = (200,) if quick else (50, 200)
    base = pretrain_router()
    tests = {
        "seen templates": generate(400 if quick else 600, "train", 9999),
        "unseen templates": generate(400 if quick else 600, "test", 9998),
    }
    rows = []
    for data_split in ("train", "train+aug"):
        for n in sizes:
            acc: dict[tuple, list] = {}
            for s in seeds:
                tr = generate(n, data_split, 100 + n + s)
                tagger = Tagger().fit(tr)
                for rname in ("lora", "scratch"):
                    router = train_router(base, tr, rname, s)
                    for pol in ("tuned", "gated"):
                        for tname, ts in tests.items():
                            r = evaluate(pol, ts, router, tagger)
                            acc.setdefault((rname, pol, tname), []).append(r)
            for (rname, pol, tname), rs in acc.items():
                rows.append({"train_templates": data_split, "labelled": n, "router": rname, "policy": pol, "test": tname, **{m: round(float(np.mean([r[m] for r in rs])), 4) for m in METRICS}})  # fmt: skip
    base_rows = {tname: evaluate("baseline", ts, None, None) for tname, ts in tests.items()}
    return {"rows": rows, "baseline": base_rows, "gate_audit": gate_audit(), "tools": 6}


def render(res: dict) -> str:
    def pick(**kw):
        return [r for r in res["rows"] if all(r[k] == v for k, v in kw.items())]

    big = max(r["labelled"] for r in res["rows"])
    md = [
        "# Results (POC 019: REAL CPU training of a LoRA router + slot tagger + strict schema gate on synthetic utterances; no language model)",
        f"6 tools with JSON Schemas plus abstention; templates for training and testing are DISJOINT families ('unseen templates' = paraphrases never trained on). Averaged over seeds; {big} labelled utterances unless stated.",
        "\n## Keyword/regex baseline (stands in for an untuned, prompted model)\n",
        markdown_table(
            ["test"] + list(METRICS),
            [[t] + [b[m] for m in METRICS] for t, b in res["baseline"].items()],
        ),
        f"\n## Tuned router + tagger, {big} labelled utterances\n",
    ]
    for rname in ("lora", "scratch"):
        rows = [r for r in pick(labelled=big, router=rname)]
        md.append(f"\n**router: {rname}**\n")
        md.append(
            markdown_table(
                ["train templates", "policy", "test"] + list(METRICS),
                [
                    [r["train_templates"], r["policy"], r["test"]] + [r[m] for m in METRICS]
                    for r in rows
                ],
            )
        )
    g = res["gate_audit"]
    md += [
        "\n## Schema gate audit\n",
        f"{g['valid_calls_accepted']}/{g['valid_calls']} valid gold calls accepted; {g['mutations_rejected']}/{g['mutations']} guaranteed-invalid mutations rejected ({g['by_kind']}); {g['malformed_rejected']}/{g['malformed_outputs']} malformed raw outputs rejected (truncated, single-quoted, trailing comma, chatty prefix, markdown fence, wrong types, oversize).",
        "",
    ]
    return "\n".join(md)


def main() -> None:
    res = run(bool(os.environ.get("DEMO_QUICK")))
    out = POC_DIR / "results"
    out.mkdir(exist_ok=True)
    write_json(out / "metrics.json", res)
    (out / "results.md").write_text(render(res))
    print(render(res))


if __name__ == "__main__":
    main()
