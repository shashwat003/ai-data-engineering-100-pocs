"""A small, dependency-free secret scanner: patterns + entropy, allowlist pragma, baseline, redacted output."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

PRAGMAS = ("pragma: allowlist secret", "gitleaks:allow")  # detect-secrets and gitleaks conventions
MAX_FILE_BYTES = 1_000_000

# (name, regex, optional capture group holding the secret)
RULES: list[tuple[str, re.Pattern, int]] = [
    ("aws-access-key", re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b"), 1),
    ("github-token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,})\b"), 1),
    ("github-fine-grained", re.compile(r"\b(github_pat_[A-Za-z0-9_]{50,})\b"), 1),
    ("slack-token", re.compile(r"\b(xox[abprs]-[A-Za-z0-9-]{20,})\b"), 1),
    ("stripe-key", re.compile(r"\b((?:sk|rk)_live_[A-Za-z0-9]{20,})\b"), 1),
    ("openai-style-key", re.compile(r"\b(sk-[A-Za-z0-9_-]{32,})\b"), 1),
    (
        "private-key-block",
        re.compile(r"(-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY(?: BLOCK)?-----)"),
        1,
    ),
    (
        "jwt",
        re.compile(r"\b(eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b"),
        1,
    ),
    ("db-url-password", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:@/]+:([^\s:@/]{6,})@[^\s/]+"), 1),
]
GENERIC = re.compile(
    r"""(?i)\b[\w.-]*(?:secret|token|passw(?:or)?d|api[_-]?key|private[_-]?key|auth)[\w.-]*["']?\s*[:=]\s*["']?([A-Za-z0-9/+_=.-]{16,})["']?"""
)
BENIGN_VALUE = re.compile(
    r"(?i)^(?:[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64})$|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
PLACEHOLDER = re.compile(
    r"(?i)example|placeholder|changeme|dummy|your[_-]|<.*>|\$\{|xxxx|\*{4}|test[_-]?value|redacted"
)
EXAMPLE_AWS = {"AKIAIOSFODNN7" + "EXAMPLE"}  # AWS documentation example key


def entropy(s: str) -> float:
    if not s:
        return 0.0
    return -sum((c / len(s)) * math.log2(c / len(s)) for c in (s.count(ch) for ch in set(s)))


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    fingerprint: str  # salted-free hash of rule+value: stable, not reversible to the secret
    preview: str  # redacted

    def key(self) -> str:
        return f"{self.path}:{self.rule}:{self.fingerprint}"


def redact(v: str) -> str:
    return v[:4] + "…" + f"({len(v)} chars)" if len(v) > 8 else "…"


def scan_text(text: str, path: str = "<mem>", entropy_min: float = 3.5) -> list[Finding]:
    out: list[Finding] = []
    for no, line in enumerate(text.splitlines(), 1):
        if any(p in line for p in PRAGMAS) or len(line) > 5000:
            continue
        hit_spans: list[tuple[int, int]] = []
        for rule, rx, grp in RULES:
            for m in rx.finditer(line):
                v = m.group(grp)
                if v in EXAMPLE_AWS or (rule == "db-url-password" and PLACEHOLDER.search(v)):
                    continue
                out.append(
                    Finding(
                        path,
                        no,
                        rule,
                        hashlib.sha256(f"{rule}:{v}".encode()).hexdigest()[:12],
                        redact(v),
                    )
                )
                hit_spans.append(m.span())
        for m in GENERIC.finditer(line):
            if any(a <= m.start() < b for a, b in hit_spans):
                continue
            v = m.group(1)
            if BENIGN_VALUE.match(v) or PLACEHOLDER.search(v) or entropy(v) < entropy_min:
                continue
            out.append(
                Finding(
                    path,
                    no,
                    "generic-high-entropy",
                    hashlib.sha256(f"generic:{v}".encode()).hexdigest()[:12],
                    redact(v),
                )
            )
    return out


def scan_file(p: Path, root: Path | None = None) -> list[Finding]:
    try:
        if p.stat().st_size > MAX_FILE_BYTES:
            return []
        data = p.read_bytes()
    except OSError:
        return []
    if b"\0" in data[:2048]:
        return []  # binary
    return scan_text(
        data.decode("utf-8", errors="replace"), str(p.relative_to(root)) if root else str(p)
    )


def scan_paths(paths: list[Path], root: Path | None = None) -> list[Finding]:
    res: list[Finding] = []
    for p in paths:
        res += scan_file(p, root)
    return res


def load_baseline(path: Path) -> set[str]:
    return set(json.loads(path.read_text())["accepted"]) if path.exists() else set()


def write_baseline(path: Path, findings: list[Finding]) -> None:
    path.write_text(json.dumps({"accepted": sorted(f.key() for f in findings)}, indent=2) + "\n")


def new_findings(findings: list[Finding], baseline: set[str]) -> list[Finding]:
    return [f for f in findings if f.key() not in baseline]
