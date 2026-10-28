#!/usr/bin/env python3
"""Run `make demo` for POCs. --fast skips POCs marked .heavy-demo; --heavy runs only those.

Usage: python scripts/run_demos.py [--fast|--heavy|--own-env]
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
failed = []
for d in sorted((ROOT / "pocs").glob("*/")):
    heavy = (d / ".heavy-demo").exists()
    if (d / ".own-env").exists() != ("--own-env" in sys.argv):
        continue  # own-env POCs (separate venv) run only with --own-env
    if ("--fast" in sys.argv and heavy) or ("--heavy" in sys.argv and not heavy):
        continue
    print(f"demo {d.name}", flush=True)
    if subprocess.run(["make", "-C", str(d), "demo"], stdout=subprocess.DEVNULL).returncode != 0:
        failed.append(d.name)
print("DEMOS FAILED: " + ", ".join(failed) if failed else "ALL DEMOS PASSED")
sys.exit(1 if failed else 0)
