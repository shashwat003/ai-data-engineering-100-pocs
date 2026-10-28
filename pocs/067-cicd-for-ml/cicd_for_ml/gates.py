"""Quality gates that CI runs after `dvc repro`. Each returns a list of problems (empty = pass)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml


def data_gate(project: Path) -> list[str]:
    problems = []
    rows = {}
    for name in ("train", "test"):
        with (project / "data" / f"{name}.csv").open() as f:
            r = list(csv.DictReader(f))
        rows[name] = r
        if not r:
            problems.append(f"{name}.csv is empty")
            continue
        if set(r[0]) != {"id", "x0", "x1", "x2", "x3", "x4", "y"}:
            problems.append(f"{name}.csv has unexpected columns {sorted(r[0])}")
            continue  # further checks assume the schema
        if any(row["y"] not in ("0", "1") for row in r):
            problems.append(f"{name}.csv has non-binary labels")
        if any(not -10 < float(row[f"x{i}"]) < 10 for row in r for i in range(5)):
            problems.append(f"{name}.csv has out-of-range features")
        pos = sum(row["y"] == "1" for row in r) / len(r)
        if not 0.2 < pos < 0.8:
            problems.append(f"{name}.csv label balance {pos:.2f} outside 0.2-0.8")
    overlap = {r["id"] for r in rows["train"]} & {r["id"] for r in rows["test"]}
    if overlap:
        problems.append(f"{len(overlap)} ids appear in both train and test (leakage)")
    return problems


def model_gate(project: Path, baseline: Path) -> list[str]:
    params = yaml.safe_load((project / "params.yaml").read_text())["gates"]
    m = json.loads((project / "metrics.json").read_text())
    base = json.loads(baseline.read_text())
    problems = []
    if m["auc"] < params["min_auc"]:
        problems.append(f"AUC {m['auc']:.3f} below the absolute floor {params['min_auc']}")
    if base["auc"] - m["auc"] > params["max_regression_vs_baseline"]:
        problems.append(
            f"AUC {m['auc']:.3f} regressed more than {params['max_regression_vs_baseline']} from baseline {base['auc']:.3f}"
        )
    return problems


def workflow_gate(path: Path) -> list[str]:
    """Static hygiene for the GitHub Actions workflow that runs this pipeline."""
    wf = yaml.safe_load(path.read_text())
    problems = []
    on = wf.get("on") or wf.get(True)
    if not on:
        problems.append("workflow has no trigger")
    if wf.get("permissions") != {"contents": "read"}:
        problems.append("permissions must be exactly contents: read")
    for job in wf["jobs"].values():
        for step in job["steps"]:
            use = step.get("uses")
            if use and "@" not in use:
                problems.append(f"unpinned action {use}")
            elif use and use.split("@")[1] in ("main", "master", "latest"):
                problems.append(f"action {use} pinned to a moving ref")
    if "pull_request_target" in (on if isinstance(on, dict) else {}):
        problems.append("pull_request_target runs untrusted code with secrets")
    return problems
