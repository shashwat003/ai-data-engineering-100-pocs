#!/usr/bin/env python3
"""Check relative Markdown links and #anchors in the root files, docs/, profile/ and every POC README (stdlib only).

External (http/https/mailto) links are not fetched. Anchors follow GitHub's rule: lower-case, drop punctuation, spaces to hyphens.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEAD = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$", re.M)


def anchors(md: Path) -> set[str]:
    text = re.sub(r"```.*?```", "", md.read_text(encoding="utf-8"), flags=re.S)
    out = set()
    for h in HEAD.findall(text):
        h = re.sub(r"[`*_]|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", h)
        out.add(re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-"))
    return out


def main() -> int:
    files = [
        *ROOT.glob("*.md"),
        *ROOT.glob("docs/*.md"),
        *ROOT.glob("profile/*.md"),
        *ROOT.glob("pocs/*/README.md"),
    ]
    bad, n = [], 0
    cache: dict[Path, set[str]] = {}
    for md in sorted(files):
        text = re.sub(r"```.*?```", "", md.read_text(encoding="utf-8"), flags=re.S)
        for target in LINK.findall(text):
            if re.match(r"^(https?:|mailto:)", target):
                continue
            n += 1
            path, _, frag = target.partition("#")
            dest = md if not path else (md.parent / path).resolve()
            if not dest.exists():
                bad.append(f"{md.relative_to(ROOT)}: missing target {target}")
                continue
            if frag and dest.suffix == ".md":
                if dest not in cache:
                    cache[dest] = anchors(dest)
                if frag.lower() not in cache[dest]:
                    bad.append(f"{md.relative_to(ROOT)}: missing anchor {target}")
    print(f"{n} relative links in {len(files)} Markdown files, {len(bad)} broken")
    for b in bad[:40]:
        print("BROKEN", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
