"""Command line gate: lint contracts, validate data, diff versions, check a git checkout.

Exit status: 0 = pass, 1 = contract/data/evolution violations, 2 = usage or file errors.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .contract import Contract, ContractError, load_contract, parse_contract
from .evolution import check_evolution
from .report import evolution_markdown, report_markdown
from .validate import validate_dataset


def git_show(repo: Path, ref: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{path}"], capture_output=True, text=True
    )
    return proc.stdout if proc.returncode == 0 else None


def git_deleted(repo: Path, ref: str, directory: str) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", "--diff-filter=D", ref, "--", directory],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return [f for f in proc.stdout.split() if f.endswith((".yaml", ".yml"))]


def check_repo(repo: Path, contracts_dir: str, base_ref: str) -> tuple[bool, list[str]]:
    """Lint every contract and require a correct version bump versus `base_ref`."""
    lines, ok = [], True
    for path in sorted((repo / contracts_dir).glob("*.y*ml")):
        rel = path.relative_to(repo).as_posix()
        try:
            new = load_contract(path)
        except ContractError as exc:
            lines.append(f"FAIL {rel}: {exc}")
            ok = False
            continue
        old_text = git_show(repo, base_ref, rel)
        if old_text is None:
            lines.append(
                f"ok   {rel}: new contract {new.id} v{new.version} (no base version to compare)"
            )
            continue
        try:
            old = parse_contract(old_text)
        except ContractError as exc:
            lines.append(f"FAIL {rel}: base version is itself invalid: {exc}")
            ok = False
            continue
        result = check_evolution(old, new)
        lines.append(
            ("ok   " if result.ok else "FAIL ")
            + rel
            + ": "
            + evolution_markdown(old.version, new.version, result)
        )
        ok &= result.ok
    for rel in git_deleted(repo, base_ref, contracts_dir):
        lines.append(
            f"FAIL {rel}: contract deleted; consumers lose the dataset (treat as MAJOR, retire explicitly)"
        )
        ok = False
    return ok, lines


def _contract(path: str) -> Contract:
    return load_contract(path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="data_contracts_ci_gate.cli", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="validate a data file against a contract")
    v.add_argument("contract")
    v.add_argument("data")
    v.add_argument(
        "--as-of", type=date.fromisoformat, default=None, help="reference date for freshness"
    )
    v.add_argument("--json", type=Path, help="also write the report as JSON")
    d = sub.add_parser("diff", help="classify the change between two contract files")
    d.add_argument("old")
    d.add_argument("new")
    lint = sub.add_parser("lint", help="parse contracts strictly")
    lint.add_argument("files", nargs="+")
    c = sub.add_parser("check", help="lint all contracts and check evolution against a git ref")
    c.add_argument("--repo", type=Path, default=Path("."))
    c.add_argument("--contracts-dir", default="contracts")
    c.add_argument("--base-ref", default="origin/main")
    args = p.parse_args(argv)

    try:
        if args.cmd == "validate":
            report = validate_dataset(_contract(args.contract), args.data, as_of=args.as_of)
            print(report_markdown(report))
            if args.json:
                args.json.write_text(json.dumps(asdict(report), indent=2, default=str) + "\n")
            return 0 if report.passed else 1
        if args.cmd == "diff":
            old, new = _contract(args.old), _contract(args.new)
            result = check_evolution(old, new)
            print(evolution_markdown(old.version, new.version, result))
            return 0 if result.ok else 1
        if args.cmd == "lint":
            for f in args.files:
                c_ = _contract(f)
                print(f"ok   {f}: {c_.id} v{c_.version}, {len(c_.columns)} columns")
            return 0
        ok, lines = check_repo(args.repo, args.contracts_dir, args.base_ref)
        print("\n\n".join(lines) if lines else "no contracts found")
        return 0 if ok else 1
    except (ContractError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
