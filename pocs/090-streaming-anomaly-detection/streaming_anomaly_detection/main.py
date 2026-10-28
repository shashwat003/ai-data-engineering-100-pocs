"""POC 090: online anomaly detectors on labelled synthetic metric streams, thresholds fixed on clean data."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .evaluate import run

POC_DIR = Path(__file__).resolve().parent.parent


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "results.json", r)
    kinds = ["spike", "level_shift", "variance", "dropout", "seasonal_break"]
    rows = [
        (
            n,
            d["event_recall"],
            d["false_alarms_per_1000"],
            d["median_delay_points"],
            *[d["recall_by_kind"].get(k, "-") for k in kinds],
            d["points_per_second"],
        )
        for n, d in r["detectors"].items()
    ]
    md = (
        f"# Results: 6 detectors on {len(r['seeds'])} synthetic streams (5-minute points, daily seasonality; {r['n_events']} injected events in total)\n\n"
        f"Every threshold was set on separate anomaly-free streams to give at most {r['false_alarm_budget_per_1000']} false alarm event per 1000 points; "
        "no threshold was tuned on the evaluation streams. An event counts as found if an alarm overlaps it (24 points of tolerance after its end).\n\n"
        + markdown_table(
            [
                "detector",
                "event recall",
                "false alarms / 1000 pts",
                "median delay (points)",
                *kinds,
                "points/s",
            ],
            rows,
        )
        + "\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
