"""POC 089: forecasting benchmark on synthetic monthly series, rolling origins, MASE with bootstrap CI."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .evaluate import run
from .series import PATTERNS

POC_DIR = Path(__file__).resolve().parent.parent


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "results.json", r)
    rows = [
        (n, m["smape"], m["mase"], r["mean_rank"][n], *[m["mase_by_pattern"][p] for p in PATTERNS])
        for n, m in sorted(r["models"].items(), key=lambda kv: kv[1]["mase"])
    ]
    md = (
        f"# Results: {r['n_series']} synthetic monthly series x {r['origins']} rolling origins, horizon {r['horizon']}\n\n"
        "MASE is scaled by the in-sample seasonal-naive error (lower is better; 1.0 equals seasonal naive in-sample). The global LightGBM model is trained only on data before each cut. "
        "Foundation models: not run (no weights or network).\n\n"
        + markdown_table(["model", "sMAPE %", "MASE", "mean rank", *PATTERNS], rows)
        + f"\n\nBest by MASE: {r['best_model']}. Paired bootstrap 95% interval for its MASE minus seasonal naive's: {r['best_vs_seasonal_naive_mase_diff_ci95']} (negative = better).\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
