"""CLI: build (site) | check (repo consistency) | stats | new (scaffold a POC from the catalogue)."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .model import MATURITY, check_repo, load_repo
from .site import build_site


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="poc_portfolio_site")
    ap.add_argument("--root", default=".", help="repository root (contains catalogue/pocs.csv)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", default="site")
    b.add_argument("--flagships", default="", help="comma-separated POC ids")
    sub.add_parser("check")
    sub.add_parser("stats")
    n = sub.add_parser("new")
    n.add_argument("id", type=int)
    a = ap.parse_args(argv)
    root = Path(a.root)
    try:
        if not (root / "catalogue" / "pocs.csv").exists():
            print("ERROR: no catalogue/pocs.csv under --root", file=sys.stderr)
            return 2
        if a.cmd == "new":
            script = root / "scripts" / "new_poc.py"
            return subprocess.run(
                [sys.executable, str(script), str(a.id)], check=False
            ).returncode  # id is an int: no shell, no injection
        pocs = load_repo(root)
        if a.cmd == "check":
            msgs = check_repo(pocs)
            for m in msgs:
                print("ERROR", m)
            print(f"{len(msgs)} problem(s)")
            return 1 if msgs else 0
        if a.cmd == "stats":
            for k in MATURITY.values():
                print(k, sum(MATURITY[p.maturity] == k for p in pocs))
            return 0
        flag = {int(x) for x in a.flagships.split(",") if x.strip()}
        print(build_site(pocs, a.out, flagships=flag))
        return 0
    except (OSError, ValueError, KeyError) as e:
        print("ERROR", e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
