"""POC 096: hierarchical forecast reconciliation (bottom-up, top-down, OLS, WLS, MinT-shrink) on a synthetic sales tree."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .evaluate import multi_seed, run

POC_DIR = Path(__file__).resolve().parent.parent


def main() -> None:
    r = run()
    r["multi_seed"] = multi_seed()
    write_json(POC_DIR / "results" / "results.json", r)
    rows = [
        (
            n,
            m["rmsse_by_level"]["total"],
            m["rmsse_by_level"]["region"],
            m["rmsse_by_level"]["store"],
            m["rmsse_by_level"]["all_levels_mean"],
            f"{m['max_coherence_gap']:.1e}",
        )
        for n, m in r["methods"].items()
    ]
    md = (
        f"# Results: {r['series']} series ({r['bottom_series']} stores) x {r['origins']} rolling origins, horizon {r['horizon']}\n\n"
        "Base forecasts are damped-trend seasonal Holt-Winters fitted to every series separately. RMSSE is scaled by the in-sample one-step naive error (lower is better). "
        "Coherence gap = largest violation of 'parent equals sum of children' (0 means coherent).\n\n"
        + markdown_table(
            ["method", "total", "region", "store", "mean over levels", "max coherence gap"], rows
        )
        + f"\n\nAcross {len(r['multi_seed']['seeds'])} different random hierarchies ({r['multi_seed']['n_obs']} observations, {r['multi_seed']['origins']} origins), change in mean RMSSE versus the base forecasts (negative = better):\n\n"
        + markdown_table(
            ["method", "mean change %", "95% bootstrap CI", "better than base in"],
            [
                (n, v["mean_change_pct"], v["ci95"], v["better_than_base_in"])
                for n, v in r["multi_seed"]["methods"].items()
            ],
        )
        + f"\n\nBest reconciled method on the first table: {r['best_reconciled']} ({r['best_vs_base_pct']}% versus the incoherent base forecasts on the mean RMSSE).\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
