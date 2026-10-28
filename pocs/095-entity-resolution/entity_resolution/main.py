"""POC 095: entity resolution with Splink on DuckDB versus exact and fuzzy rule baselines."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .data import make
from .resolve import evaluate, scaling

POC_DIR = Path(__file__).resolve().parent.parent


def main() -> None:
    r = evaluate(make())
    r["scaling"] = scaling()
    write_json(POC_DIR / "results" / "results.json", r)
    rows = [
        (n, m["precision"], m["recall"], m["f1"], m["bcubed_f1"], m["clusters"], m["pairs"])
        for n, m in r["methods"].items()
    ]
    md = (
        f"# Results: {r['n_records']} synthetic person records, {r['n_entities']} true entities, {r['true_pairs']} true duplicate pairs\n\n"
        f"Splink (probabilistic, DuckDB) trained without labels: u-probabilities from random sampling, m-probabilities by EM. Blocking scored {r['splink_scored_pairs']:,} candidate pairs instead of "
        f"{r['all_possible_pairs']:,} possible (blocking pair recall {r['blocking_pair_recall']}). Training and prediction took {r['splink_seconds']} s.\n\n"
        + markdown_table(
            [
                "method",
                "pair precision",
                "pair recall",
                "pair F1",
                "B-cubed F1",
                "clusters",
                "pairs linked",
            ],
            rows,
        )
        + "\n\nScaling of blocking:\n\n"
        + markdown_table(
            ["records", "all possible pairs", "pairs scored after blocking", "seconds"],
            [
                (x["records"], f"{x['possible_pairs']:,}", f"{x['scored_pairs']:,}", x["seconds"])
                for x in r["scaling"]
            ],
        )
        + "\n\nClusters are connected components of the linked pairs; B-cubed scores the resulting entities, which punishes chains that merge different people.\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
