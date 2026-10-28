"""POC 076: find the reasons an experiment is not reproducible, and detect environment, code and data drift."""

from __future__ import annotations

import tempfile
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .check import check, diagnose
from .cli import TRACKED
from .manifest import build_manifest, diff_manifests

POC_DIR = Path(__file__).resolve().parent.parent


def run() -> dict:
    cfg = {"seed": 7}
    results = {}
    for name in (
        "seeded",
        "linear",
        "unseeded",
        "hash_order",
        "hash_order_fixed",
        "thread_race",
        "thread_fixed",
        "clock_id",
    ):
        c = check(name, cfg)
        d = diagnose(name, cfg)
        results[name] = {
            "reproducible": c["reproducible"],
            "distinct_outputs": c["distinct_outputs"],
            "likely_causes": d["likely_causes"],
        }
    with tempfile.TemporaryDirectory() as t:
        data = Path(t) / "train.csv"
        data.write_text("a,b\n1,2\n3,4\n")

        def mk(cfg_, seed=7):
            return build_manifest(
                cfg_, seed, [data], POC_DIR / "reproducibility_kit", TRACKED, None
            )  # no git state: it varies with the checkout

        base = mk({"experiment": "demo", "lr": 0.1})
        same = diff_manifests(base, mk({"experiment": "demo", "lr": 0.1}))
        cfg_changed = diff_manifests(base, mk({"experiment": "demo", "lr": 0.2}))
        seed_changed = diff_manifests(base, mk({"experiment": "demo", "lr": 0.1}, seed=8))
        pkg_changed = diff_manifests(
            base, {**base, "packages": {**base["packages"], "numpy": "0.0.1"}}
        )
        data.write_text("a,b\n1,2\n3,5\n")
        data_changed = diff_manifests(base, mk({"experiment": "demo", "lr": 0.1}))
    return {
        "experiments": results,
        "manifest_id": base["manifest_id"],
        "drift": {
            "nothing changed": same,
            "data file edited": data_changed,
            "config edited": cfg_changed,
            "seed changed": seed_changed,
            "package version differs": pkg_changed,
        },
        "tracked_packages": base["packages"],
    }


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "results.json", r)
    rows = [
        (
            n,
            "yes" if v["reproducible"] else "NO",
            v["distinct_outputs"],
            "; ".join(v["likely_causes"]) or "-",
        )
        for n, v in r["experiments"].items()
    ]
    drift_rows = [
        (k, ", ".join(f"{d['severity']}:{d['field']}" for d in v) or "none")
        for k, v in r["drift"].items()
    ]
    md = (
        "# Results: reproducibility checks (each experiment run in fresh processes, varying PYTHONHASHSEED and thread count; diagnosis repeats each run and varies one factor)\n\n"
        + markdown_table(
            ["experiment", "identical across 4 runs", "distinct outputs", "diagnosed cause"], rows
        )
        + "\n\nManifest drift detection:\n\n"
        + markdown_table(["change made", "drift reported"], drift_rows)
        + f"\n\nManifest id of the baseline: {r['manifest_id']}. Tracked packages: {r['tracked_packages']}.\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
