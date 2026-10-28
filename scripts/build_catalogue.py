#!/usr/bin/env python3
"""Validate catalogue/pocs.csv and render docs/CATALOGUE.md (stdlib only)."""

import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
rows = list(csv.DictReader((ROOT / "catalogue/pocs.csv").open()))

errors = []
if len(rows) != 100:
    errors.append(f"expected 100 rows, found {len(rows)}")
if [int(r["id"]) for r in rows] != list(range(1, len(rows) + 1)):
    errors.append("ids must be contiguous from 1")
for key in ("slug", "title"):
    dupes = [v for v, n in Counter(r[key] for r in rows).items() if n > 1]
    if dupes:
        errors.append(f"duplicate {key}: {dupes}")
for r in rows:
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", r["slug"]):
        errors.append(f"bad slug: {r['slug']}")
    if r["runs_local"] not in {"yes", "partial", "aws"}:
        errors.append(f"bad runs_local for {r['slug']}")
if errors:
    sys.exit("\n".join(errors))


def poc_dir(r):
    return ROOT / "pocs" / f"{int(r['id']):03d}-{r['slug']}"


def pending(r):
    """Implemented as a scope-adjusted variant but awaiting the owner's approval: not counted as built."""
    return (poc_dir(r) / ".pending-approval").exists()


def built(r):
    return (poc_dir(r) / "README.md").exists() and not pending(r)


n_built = sum(built(r) for r in rows)
by_cat = defaultdict(list)
for r in rows:
    by_cat[r["category"]].append(r)

out = [
    "# POC Catalogue (generated from `catalogue/pocs.csv` - do not edit by hand)\n",
    f"{len(rows)} POCs, **{n_built} built**. `runs_local`: **yes** = no cloud account needed, "
    "**partial** = core runs locally, GPU/extra services optional, "
    "**aws** = needs an AWS account (cost-guarded, `make destroy` provided).\n",
    "| Category | Count |",
    "|---|---|",
]
out += [f"| {c} | {len(v)} |" for c, v in by_cat.items()]
for cat, items in by_cat.items():
    out += [
        f"\n## {cat}\n",
        "| # | POC | What it proves | Stack | Runs | Wave | Fidelity | Status |",
        "|---|---|---|---|---|---|---|---|",
    ]
    out += [
        f"| {r['id']} | `{r['slug']}` | {r['title']} | {r['stack']} | {r['runs_local']} | {r['wave']} | {r.get('fidelity', 'full')} | {'built' if built(r) else ('pending approval' if pending(r) else 'planned')} |"
        for r in items
    ]


def built_table(link):
    lines = ["| POC | What it shows | Evidence |", "|---|---|---|"]
    for r in rows:
        if built(r):
            lines.append(
                f"| [{int(r['id']):03d} {r['slug']}]({link(r)}) | {r['title']} | {r['evidence']} |"
            )
    return "\n".join(lines)


def inject(path, marker, body):
    text = path.read_text()
    pattern = re.compile(rf"(<!-- {marker}:START -->\n).*?(<!-- {marker}:END -->)", re.DOTALL)
    if pattern.search(text):
        path.write_text(pattern.sub(lambda m: f"{m.group(1)}{body}\n{m.group(2)}", text))


(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs/CATALOGUE.md").write_text("\n".join(out) + "\n")
print(
    f"OK: {len(rows)} POCs ({n_built} built), {len(by_cat)} categories, "
    f"waves={dict(sorted(Counter(r['wave'] for r in rows).items()))}"
)
