#!/usr/bin/env python3
"""Fail if tracked files contain obvious secret patterns. Deterministic, offline, no dependencies."""

import re
import subprocess
import sys
from pathlib import Path

PATTERNS = {
    "AWS access key id": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "private key block": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "generic API key assignment": re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"][A-Za-z0-9/+_\-]{20,}['\"]"
    ),
    "OpenAI/Anthropic style key": re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
}


def list_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return [f for f in out.split("\0") if f]
    except (subprocess.CalledProcessError, FileNotFoundError):  # not a git checkout (e.g. tarball)
        skip = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
        return [str(p) for p in Path(".").rglob("*") if p.is_file() and not skip & set(p.parts)]


files = list_files()
hits = []
for name in files:
    try:
        text = open(name, encoding="utf-8").read()
    except (UnicodeDecodeError, OSError):
        continue
    for label, pattern in PATTERNS.items():
        for m in pattern.finditer(text):
            hits.append(f"{name}: {label}: {m.group(0)[:12]}...")
if hits:
    print("Possible secrets found:\n" + "\n".join(hits))
    sys.exit(1)
print(f"secret scan clean ({len(files)} files)")
