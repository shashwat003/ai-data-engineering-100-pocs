#!/usr/bin/env python3
"""Run pytest for libs/ and every pocs/*/ in its own process (POCs may reuse module names).

Usage: python scripts/run_tests.py [--fast|--heavy|--own-env] [poc-dir-name ...]   # no args = libs + all POCs
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
targets = [ROOT / "libs"] + sorted(
    p for p in (ROOT / "pocs").glob("*/") if (p / "pytest.ini").exists()
)
args = [a for a in sys.argv[1:] if not a.startswith("--")]
own_env = [
    t for t in targets if (t / ".own-env").exists()
]  # POCs needing their own venv (e.g. Airflow): only via --own-env
targets = [t for t in targets if t not in own_env]
if "--own-env" in sys.argv:
    targets = own_env
if "--fast" in sys.argv:  # fast CI: skip POCs whose tests need Spark/Terraform/long runs
    targets = [t for t in targets if not (t / ".heavy-tests").exists()]
if "--heavy" in sys.argv:
    targets = [t for t in targets if (t / ".heavy-tests").exists()]
if args:
    targets = [t for t in targets if t.name in args]

failed = []
for target in targets:
    print(f"\n=== {target.relative_to(ROOT)} ===", flush=True)
    if subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=target).returncode != 0:
        failed.append(target.name)
print("\nFAILED: " + ", ".join(failed) if failed else "\nALL TEST SUITES PASSED")
sys.exit(1 if failed else 0)
