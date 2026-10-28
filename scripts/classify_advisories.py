#!/usr/bin/env python3
"""Classify `pip-audit -f json` output by package, impact class and relevance to the POC that uses the environment (stdlib only).

CVSS scores are NOT available offline (the OSV and GitHub advisory APIs are not reachable from the build sandbox), so
severity is not asserted: advisories are grouped by the impact class parsed from the advisory text, with the exploit
precondition and a per-package statement of whether the POC exercises the affected component.

Usage: python scripts/classify_advisories.py audit.json [imported_packages.txt]
"""

import collections
import json
import re
import sys

IMPACT = [
    (
        "remote code execution",
        r"remote code|arbitrary code|code execution|command injection|deserializ",
    ),
    (
        "authentication / authorisation bypass",
        r"authenticat|authoriz|permission|privilege|access control|bypass|impersonat",
    ),
    (
        "injection / XSS / SSRF",
        r"inject|cross-site|xss|ssrf|server-side request|open redirect|path traversal|directory traversal|header",
    ),
    ("information disclosure", r"disclos|leak|expos|sensitive|secret|credential|plaintext"),
    (
        "denial of service",
        r"denial|dos\b|resource exhaustion|excessive|unbounded|infinite|crash|memory|cpu|regex|redos|slow",
    ),
]
PRECONDITION = {
    "remote code execution": "needs attacker-controlled input to the affected parser/loader or a writable trusted location",
    "authentication / authorisation bypass": "needs a running multi-user API server / web UI",
    "injection / XSS / SSRF": "needs a running web UI/API or attacker-supplied URLs/headers",
    "information disclosure": "needs a running server or shared logs/connections",
    "denial of service": "needs network-reachable service or attacker-controlled input size",
    "other / unclassified": "see advisory text",
}


def classify(text: str) -> str:
    t = text.lower()
    for name, pat in IMPACT:
        if re.search(pat, t):
            return name
    return "other / unclassified"


def main() -> None:
    audit = json.load(open(sys.argv[1]))
    imported = set(open(sys.argv[2]).read().split()) if len(sys.argv) > 2 else set()
    rows = collections.defaultdict(dict)
    for dep in audit["dependencies"]:
        for v in dep.get("vulns", []):
            rows[(dep["name"], dep["version"])][v["id"]] = (v, classify(v.get("description", "")))
    total_unique = sum(len(v) for v in rows.values())
    print(f"{total_unique} unique advisories in {len(rows)} packages\n")
    print(
        "| Package | Version | Unique advisories | Impact classes | Highest fixed version | Imported by POC | Precondition(s) |"
    )
    print("|---|---|---|---|---|---|---|")
    for (name, ver), vs in sorted(rows.items()):
        classes = collections.Counter(c for _, c in vs.values())
        fixes = sorted({f for v, _ in vs.values() for f in v.get("fix_versions", [])})
        pre = "; ".join(sorted({PRECONDITION[c] for c in classes}))[:150]
        print(
            f"| {name} | {ver} | {len(vs)} | {', '.join(f'{c} x{n}' for c, n in classes.most_common())} | {fixes[-1] if fixes else 'none yet'} | {'yes' if name.replace('-', '_') in imported or name in imported else 'no'} | {pre} |"
        )


if __name__ == "__main__":
    main()
