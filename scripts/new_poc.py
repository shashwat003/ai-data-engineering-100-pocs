#!/usr/bin/env python3
"""Scaffold pocs/<NNN-slug>/ from templates/poc using catalogue/pocs.csv.

Usage: python scripts/new_poc.py <id>
"""

import csv
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
poc_id = sys.argv[1] if len(sys.argv) > 1 else sys.exit(__doc__)
row = next(
    (
        r
        for r in csv.DictReader((ROOT / "catalogue/pocs.csv").open())
        if int(r["id"]) == int(poc_id)
    ),
    None,
)
if row is None:
    sys.exit(f"no POC with id {poc_id}")

dest = ROOT / "pocs" / f"{int(row['id']):03d}-{row['slug']}"
if dest.exists():
    sys.exit(f"{dest} already exists")
shutil.copytree(ROOT / "templates/poc", dest)
pkg = row["slug"].replace("-", "_")
shutil.move(dest / "{{PKG}}", dest / pkg)
for f in dest.rglob("*"):
    if f.is_file():
        f.write_text(
            f.read_text()
            .replace("{{ID}}", f"{int(row['id']):03d}")
            .replace("{{TITLE}}", row["title"])
            .replace("{{CATEGORY}}", row["category"])
            .replace("{{STACK}}", row["stack"])
            .replace("{{SLUG}}", row["slug"])
            .replace("{{PKG}}", pkg)
        )
print(f"created {dest.relative_to(ROOT)}")
