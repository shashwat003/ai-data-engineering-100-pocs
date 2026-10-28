"""POC 037: exactly-once windowed aggregation under random crashes vs at-least-once and at-most-once, on a simulated broker."""

from __future__ import annotations

import random
from collections import Counter
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .broker import Broker
from .processor import reference, run_to_completion

POC_DIR = Path(__file__).resolve().parent.parent
POINTS = ("after_output", "before_commit", "after_batch")


def make_events(
    n: int = 600, seed: int = 4, late_fraction: float = 0.05
) -> list[tuple[str, int, int]]:
    rng = random.Random(seed)
    out, t = [], 0
    for _ in range(n):
        t += rng.randint(0, 3)
        ts = t - rng.randint(60, 200) if rng.random() < late_fraction and t > 200 else t
        out.append((rng.choice("ABC"), ts, rng.randint(1, 9)))
    return out


def load(events) -> Broker:
    b = Broker()
    for t in ("events", "windows", "late", "state"):
        b.create_topic(t)
    for key, ts, v in events:
        b.produce("events", 0, key, {"v": v}, ts)
    return b


def collect(b: Broker) -> dict:
    recs = b.fetch("windows", 0, 0, 10**9, read_committed=True)
    per = Counter((r.value["key"], r.value["window_start"]) for r in recs)
    totals: dict = {}
    for r in recs:
        c = totals.setdefault((r.value["key"], r.value["window_start"]), [0, 0])
        c[0] += r.value["count"]
        c[1] += r.value["sum"]
    late = b.fetch("late", 0, 0, 10**9, read_committed=True)
    return {
        "records": len(recs),
        "duplicates": sum(c - 1 for c in per.values()),
        "totals": {k: tuple(v) for k, v in totals.items()},
        "late": len(late),
    }


def score(b: Broker, events) -> dict:
    ref, ref_late = reference(events)
    got = collect(b)
    wrong = sum(1 for k, v in got["totals"].items() if ref.get(k) != v)
    missing = sum(1 for k in ref if k not in got["totals"])
    return {
        "windows_expected": len(ref),
        "output_records": got["records"],
        "duplicate_records": got["duplicates"],
        "windows_with_wrong_values": wrong,
        "windows_missing": missing,
        "late_events": got["late"],
        "late_expected": len(ref_late),
        "exact": got["duplicates"] == 0
        and wrong == 0
        and missing == 0
        and got["late"] == len(ref_late),
    }


def random_plan(rng: random.Random, n_crashes: int, steps: int) -> set[tuple[int, str]]:
    return {(rng.randint(1, steps), rng.choice(POINTS)) for _ in range(n_crashes)}


def run(trials: int = 30) -> dict:
    events = make_events()
    steps = -(-len(events) // 25)
    res: dict = {"events": len(events), "batches": steps, "trials": trials, "modes": {}}
    for mode in ("exactly_once", "at_least_once", "at_most_once"):
        rng = random.Random(11)
        exact = dup = wrong = missing = restarts = 0
        for _ in range(trials):
            b = load(events)
            restarts += run_to_completion(b, mode, random_plan(rng, 4, steps))
            sc = score(b, events)
            exact += sc["exact"]
            dup += sc["duplicate_records"]
            wrong += sc["windows_with_wrong_values"]
            missing += sc["windows_missing"]
        res["modes"][mode] = {
            "runs_exactly_correct": exact,
            "total_duplicate_records": dup,
            "total_windows_with_wrong_values": wrong,
            "total_windows_missing": missing,
            "restarts": restarts,
        }
    b = load(events)
    run_to_completion(b, "exactly_once")
    res["no_crash_exactly_once"] = score(b, events)
    return res


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    rows = [
        [
            m,
            f"{v['runs_exactly_correct']}/{r['trials']}",
            v["total_duplicate_records"],
            v["total_windows_with_wrong_values"],
            v["total_windows_missing"],
            v["restarts"],
        ]
        for m, v in r["modes"].items()
    ]
    md = ["# Results (measured by `python -m exactly_once_windows.main`; SIMULATED in-process broker, no Kafka/Redpanda; 4 random crashes per run)", "", f"{r['events']} events in {r['batches']} batches of 25; 60 s tumbling windows, 30 s allowed lateness, ~5% late events. {r['trials']} runs per mode.", "",
          markdown_table(["delivery mode", "runs exactly equal to reference", "duplicate window records", "windows with wrong values", "windows missing", "restarts"], rows), "", f"No-crash exactly-once run vs the independent sequential reference: {r['no_crash_exactly_once']}"]  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
