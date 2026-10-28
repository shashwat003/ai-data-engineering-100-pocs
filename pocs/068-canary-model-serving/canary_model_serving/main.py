"""POC 068: progressive delivery with statistical guardrails and automatic rollback, simulated over many seeds."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from pocs_common import write_json
from pocs_common.report import markdown_table

from .controller import big_bang, rollout
from .sim import SCENARIOS

POC_DIR = Path(__file__).resolve().parent.parent
SEEDS = 200


def run(seeds: int = SEEDS) -> dict:
    res = {}
    for name, cand in SCENARIOS.items():
        outs = [rollout(cand, np.random.default_rng(1000 + s)) for s in range(seeds)]
        rb = [o for o in outs if o.result == "rolled_back"]
        bb = big_bang(cand)
        res[name] = {
            "rollback_rate": round(len(rb) / seeds, 3),
            "rollbacks_by_deadline_not_evidence": round(
                sum("fail closed" in o.reason for o in rb) / seeds, 3
            ),
            "median_minutes_to_decision": float(np.median([o.minutes for o in outs])),
            "median_minutes_to_rollback": float(np.median([o.minutes for o in rb])) if rb else None,
            "mean_excess_errors_canary": round(float(np.mean([o.excess_errors for o in outs])), 1),
            "excess_errors_big_bang_60min": round(bb["excess_errors"], 1),
        }
    return res


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    rows = [
        [
            k,
            v["rollback_rate"],
            v["rollbacks_by_deadline_not_evidence"],
            v["median_minutes_to_decision"],
            v["median_minutes_to_rollback"] or "-",
            v["mean_excess_errors_canary"],
            v["excess_errors_big_bang_60min"],
        ]
        for k, v in r.items()
    ]
    md = ["# Results (measured by `python -m canary_model_serving.main`; traffic SIMULATED, 200 seeds per scenario, 600 req/min, looks every 5 min)", "",
          markdown_table(["candidate", "rollback rate", "of which deadline (fail closed)", "median min to decision", "median min to rollback", "mean excess errors (canary rollout)", "excess errors (big-bang, 60 min)"], rows), "",
          "A rollback \"by deadline\" means the gate could not PROVE the candidate within tolerance at the 50% step within 120 minutes, so it failed closed. Excess errors = errors beyond the baseline's rate on the same requests. A healthy candidate should (almost) never be rolled back; a regressed one should be, early."]  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
