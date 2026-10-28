"""Command line: write a run manifest, verify the current environment against one, or check an experiment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .check import check, diagnose
from .experiments import EXPERIMENTS
from .manifest import build_manifest, diff_manifests

POC = Path(__file__).resolve().parent.parent
TRACKED = ["numpy", "scikit-learn", "pandas", "scipy"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("manifest")
    m.add_argument("--out", type=Path, required=True)
    m.add_argument("--seed", type=int, default=0)
    m.add_argument("--data", type=Path, nargs="*", default=[])
    v = sub.add_parser("verify")
    v.add_argument("--manifest", type=Path, required=True)
    v.add_argument("--data", type=Path, nargs="*", default=[])
    c = sub.add_parser("check")
    c.add_argument("experiment", choices=sorted(EXPERIMENTS))
    c.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    if a.cmd == "manifest":
        man = build_manifest(
            {"experiment": "demo"},
            a.seed,
            a.data,
            POC / "reproducibility_kit",
            TRACKED,
            POC.parent.parent,
        )
        a.out.write_text(json.dumps(man, indent=2, sort_keys=True) + "\n")
        print(man["manifest_id"])
        return 0
    if a.cmd == "verify":
        old = json.loads(a.manifest.read_text())
        new = build_manifest(
            old["config"],
            old["seed"],
            a.data,
            POC / "reproducibility_kit",
            TRACKED,
            POC.parent.parent,
        )
        drift = diff_manifests(old, new)
        for d in drift:
            print(f"{d['severity']:6} {d['field']}: {d['changed'] or 'changed'}", file=sys.stderr)
        return 1 if any(d["severity"] == "high" for d in drift) else 0
    res = check(a.experiment, {"seed": a.seed})
    print(json.dumps({**res, **diagnose(a.experiment, {"seed": a.seed})}, indent=2))
    return 0 if res["reproducible"] else 1


if __name__ == "__main__":
    sys.exit(main())
