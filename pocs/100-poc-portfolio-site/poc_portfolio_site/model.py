"""Read the repository: catalogue rows, per-POC README quality, evidence statuses and a maturity level."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_SECTIONS = ("Problem", "Approach", "Run it", "Evidence status", "Results", "Limitations")
STATUS_WORDS = {
    "simulated": ("simulated",),
    "mock": ("mock",),
    "modelled": ("modelled", "modeled"),
    "unverified": ("not run", "unverified", "not tested"),
    "tested": ("tested", "measured"),
}
ALIASES = {"Approach": ("approach", "architecture", "design")}  # equivalent headings
MATURITY = {0: "planned", 1: "scaffold", 2: "tested", 3: "verified"}


@dataclass
class Poc:
    id: int
    slug: str
    title: str
    category: str
    stack: str
    runs_local: str
    wave: int
    evidence: str
    dir: Path | None = None
    sections: list[str] = field(default_factory=list)
    has_mermaid: bool = False
    limitation_count: int = 0
    n_tests: int = 0
    has_results: bool = False
    labels: set[str] = field(default_factory=set)
    markers: set[str] = field(default_factory=set)
    readme: str = ""

    @property
    def built(self) -> bool:
        return bool(self.evidence.strip())

    @property
    def missing_sections(self) -> list[str]:
        return [s for s in REQUIRED_SECTIONS if s not in self.sections]

    @property
    def maturity(self) -> int:
        if not self.built:
            return 1 if self.dir else 0
        if (
            self.n_tests
            and self.has_results
            and self.has_mermaid
            and not self.missing_sections
            and self.limitation_count
        ):
            return 3
        return 2 if self.n_tests else 1


def parse_readme(text: str) -> dict:
    heads = re.findall(r"^#{2,3}\s+(.+?)\s*$", text, flags=re.M)
    sections = [
        s
        for s in REQUIRED_SECTIONS
        if any(h.lower().startswith(a) for h in heads for a in ALIASES.get(s, (s.lower(),)))
    ]
    lim = re.search(r"^#{2,3}\s+Limitations[^\n]*\n(.*?)(?=^#{2,3}\s|\Z)", text, flags=re.M | re.S)
    n_lim = len(re.findall(r"^\s*[-*]\s+\S", lim.group(1), flags=re.M)) if lim else 0
    ev = re.search(
        r"^#{2,3}\s+Evidence status[^\n]*\n(.*?)(?=^#{2,3}\s|\Z)", text, flags=re.M | re.S
    )
    labels: set[str] = set()
    if ev:
        low = ev.group(1).lower()
        for label, words in STATUS_WORDS.items():
            if any(w in low for w in words):
                labels.add(label)
    return {
        "sections": sections,
        "mermaid": "```mermaid" in text,
        "limitations": n_lim,
        "labels": labels,
    }


def count_tests(poc_dir: Path) -> int:
    n = 0
    for f in (poc_dir / "tests").glob("test_*.py") if (poc_dir / "tests").is_dir() else []:
        n += len(
            re.findall(r"^\s*(?:async\s+)?def test_", f.read_text(errors="replace"), flags=re.M)
        )
    return n


def load_repo(root: str | Path) -> list[Poc]:
    root = Path(root)
    rows = list(
        csv.DictReader((root / "catalogue" / "pocs.csv").open(newline="", encoding="utf-8"))
    )
    pocs = []
    for r in rows:
        p = Poc(
            int(r["id"]),
            r["slug"],
            r["title"],
            r["category"],
            r["stack"],
            r["runs_local"],
            int(r["wave"]),
            r.get("evidence", ""),
        )
        d = root / "pocs" / f"{p.id:03d}-{p.slug}"
        if not d.is_dir():  # slug may have been renamed after scaffolding: match by numeric prefix
            cand = sorted((root / "pocs").glob(f"{p.id:03d}-*"))
            d = cand[0] if cand else d
        if d.is_dir():
            p.dir = d
            rd = d / "README.md"
            if rd.exists():
                p.readme = rd.read_text(encoding="utf-8", errors="replace")
                info = parse_readme(p.readme)
                p.sections, p.has_mermaid, p.limitation_count, p.labels = (
                    info["sections"],
                    info["mermaid"],
                    info["limitations"],
                    info["labels"],
                )
            p.n_tests = count_tests(d)
            p.has_results = (d / "results" / "results.md").exists()
            p.markers = {
                m.name
                for m in d.glob(".*")
                if m.name in (".heavy-tests", ".heavy-demo", ".own-env")
            }
        pocs.append(p)
    return pocs


def check_repo(pocs: list[Poc]) -> list[str]:
    """Consistency problems that should fail CI."""
    msgs = []
    seen = set()
    for p in pocs:
        if p.id in seen:
            msgs.append(f"duplicate id {p.id}")
        seen.add(p.id)
        if p.built and p.dir is None:
            msgs.append(f"POC {p.id}: marked built but has no directory")
        if p.built and p.dir is not None:
            if not p.n_tests:
                msgs.append(f"POC {p.id}: built but has no tests")
            for s in p.missing_sections:
                msgs.append(f"POC {p.id}: README lacks section '{s}'")
            if not p.has_mermaid:
                msgs.append(f"POC {p.id}: README lacks a Mermaid diagram")
            if not p.limitation_count:
                msgs.append(f"POC {p.id}: README lists no limitations")
            if not p.has_results:
                msgs.append(f"POC {p.id}: no results/results.md")
    return msgs
