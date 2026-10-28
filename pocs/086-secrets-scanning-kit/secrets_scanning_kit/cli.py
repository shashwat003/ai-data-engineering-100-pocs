"""Command line entry used by the git hook and CI: scan staged files or a directory, honour a baseline."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .scanner import load_baseline, new_findings, scan_paths, write_baseline


def staged_files(repo: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [repo / f for f in out.split("\0") if f]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scan for secrets")
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--staged", action="store_true", help="scan only staged files (pre-commit)")
    ap.add_argument("--baseline", type=Path, default=None)
    ap.add_argument("--write-baseline", action="store_true")
    a = ap.parse_args(argv)
    repo = a.repo.resolve()
    if a.staged:
        files = staged_files(repo)
    else:
        files = [p for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts]
    found = scan_paths(files, repo)
    if a.write_baseline and a.baseline:
        write_baseline(a.baseline, found)
        print(f"baseline written with {len(found)} accepted findings")
        return 0
    fresh = new_findings(found, load_baseline(a.baseline) if a.baseline else set())
    for f in fresh:
        print(f"{f.path}:{f.line}: {f.rule}: {f.preview}", file=sys.stderr)
    if fresh:
        print(
            f"{len(fresh)} possible secret(s) found. Remove them, rotate the credential, or add '# pragma: allowlist secret' if it is a false positive.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
