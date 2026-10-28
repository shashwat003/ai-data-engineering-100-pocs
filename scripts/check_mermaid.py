#!/usr/bin/env python3
"""Parse every ```mermaid block in the repository's Markdown with the real Mermaid parser (headless Chromium via Playwright).

Release check, not part of fast CI (needs Node's `mermaid` package and a Chromium install):
    npm install mermaid@11            # in any scratch directory
    python scripts/check_mermaid.py --mermaid-js <scratch>/node_modules/mermaid/dist/mermaid.min.js
"""

import argparse
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BLOCK = re.compile(r"```mermaid\n(.*?)```", re.S)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mermaid-js", required=True)
    ap.add_argument(
        "--chromium",
        default=None,
        help="path to a Chromium executable if Playwright's own is not installed",
    )
    a = ap.parse_args()
    blocks = []
    for md in sorted(
        [
            *ROOT.glob("*.md"),
            *ROOT.glob("docs/*.md"),
            *ROOT.glob("profile/*.md"),
            *ROOT.glob("pocs/*/README.md"),
        ]
    ):
        for i, m in enumerate(BLOCK.finditer(md.read_text(encoding="utf-8")), 1):
            blocks.append((str(md.relative_to(ROOT)), i, m.group(1)))
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=a.chromium) if a.chromium else p.chromium.launch()
        page = b.new_page()
        page.set_content("<html><body></body></html>")
        page.add_script_tag(path=a.mermaid_js)
        page.evaluate("mermaid.initialize({startOnLoad:false})")
        bad = []
        for f, i, text in blocks:
            err = page.evaluate(
                "async (t) => { try { await mermaid.parse(t); return null } catch (e) { return String(e.message || e).slice(0, 300) } }",
                text,
            )
            if err:
                bad.append((f, i, err))
        b.close()
    print(json.dumps({"diagrams": len(blocks), "invalid": len(bad)}))
    for f, i, err in bad:
        print(f"INVALID {f} #{i}: {err.splitlines()[0]}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
