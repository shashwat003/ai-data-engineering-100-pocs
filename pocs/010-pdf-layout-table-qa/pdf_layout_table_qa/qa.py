"""Rule-based extractive question answering over extracted pages (no LLM, no vision model).

Questions come from a small fixed template family, so answer patterns are templated too: this measures
the *extraction layer* (naive vs layout-aware), not language understanding.
"""

from __future__ import annotations

import re

from retrieval_kit import BM25Index

from .gen import COMPANIES


def num(s: str) -> str | None:
    s = s.strip()
    m = re.fullmatch(r"\(?(-?[\d,]+(?:\.\d+)?)\)?", s)
    if not m:
        return None
    v = m.group(1).replace(",", "")
    if s.startswith("("):
        v = "-" + v.lstrip("-")
    return v


def company_of(question: str) -> str | None:
    hits = [c for c in COMPANIES if c.lower() in question.lower()]
    return hits[0] if len(hits) == 1 else None


def windows(text: str, size: int = 400, stride: int = 200) -> list[str]:
    return [text[i : i + size] for i in range(0, max(1, len(text) - size + stride), stride)]


class TextQA:
    """Answers 'which year' / 'how many people' questions from page text using BM25 windows + a templated pattern."""

    def __init__(self, pages: list[str]):
        self.wins, self.ids = [], []
        for p, t in enumerate(pages, start=1):
            flat = re.sub(r"\s+", " ", t)
            for j, w in enumerate(windows(flat)):
                self.wins.append(w)
                self.ids.append(f"{p}:{j}")
        self.index = BM25Index().fit(self.ids, self.wins)

    def answer(self, q: str) -> str | None:
        city = re.search(r"the (\w+) plant", q)
        if not city:
            return None
        pat = rf"The {city.group(1)} plant opened in (\d{{4}}) and employs ([\d,]+) people"
        for did, _ in self.index.search(q, k=5):
            m = re.search(pat, self.wins[self.ids.index(did)])
            if m:
                return m.group(1) if "year" in q else m.group(2).replace(",", "")
        # patterns can straddle two windows: fall back to the whole doc text
        m = re.search(pat, re.sub(r"\s+", " ", " ".join(self.wins)))
        return None if not m else (m.group(1) if "year" in q else m.group(2).replace(",", ""))


def _tok(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


class TableQA:
    """Structure-aware: pick table by caption/header overlap, row by label, column by header text, return the exact cell."""

    def __init__(self, pages):
        self.tables = [
            (pg.page, cap, t)
            for pg in pages
            for cap, t in zip(pg.captions + [""] * len(pg.tables), pg.tables, strict=False)
        ]

    def answer(self, q: str) -> tuple[str | None, str]:
        qt = _tok(q)
        best = None
        for page, cap, t in self.tables:
            if len(t) < 2:
                continue
            score = len(_tok(cap) & qt) + len(_tok(" ".join(t[0])) & qt)
            rows = {r[0]: len(_tok(r[0]) & qt) for r in t[1:]}
            cols = {j: len(_tok(h) & qt) for j, h in enumerate(t[0]) if j}
            if rows and cols and score > 0 and (best is None or score > best[0]):
                best = (score, page, t, rows, cols)
        if not best:
            return None, ""
        _, page, t, rows, cols = best
        row = max(rows, key=lambda k: (rows[k], len(k)))
        # a row/column must actually match something in the question
        if rows[row] == 0 or max(cols.values()) == 0:
            return None, ""
        col = max(cols, key=lambda k: cols[k])
        cell = next(r[col] for r in t[1:] if r[0] == row)
        return num(cell), f"p{page}"


class FlatTableQA:
    """Baseline: find the best-matching line in flat text and map numbers to headers by position after whitespace splitting."""

    def __init__(self, pages: list[str]):
        self.lines = [(p, ln) for p, t in enumerate(pages, start=1) for ln in t.splitlines()]

    def answer(self, q: str) -> str | None:
        qt = (
            _tok(q)
            - {"what", "was", "for", "in", "at", "usd", "m", "net", "revenue", "headcount", "the"}
            - _tok(company_of(q) or "")
        )
        # candidate rows: lines with a number that are not captions or header lines
        scored = [
            (len(_tok(ln) & (qt - {"2025", "2024"})), p, ln)
            for p, ln in self.lines
            if re.search(r"\d", ln)
            and not ln.startswith("Table")
            and not re.search(r"Q\d 2025|2024 2025", ln)
        ]
        if not scored:
            return None
        _, p, row = max(scored, key=lambda x: x[0])
        header = next(
            (ln for pp, ln in self.lines if pp == p and re.search(r"(Q\d 2025|2024 2025)", ln)),
            None,
        )
        if header is None:
            return None
        heads = re.findall(r"Q\d 2025|2024|2025", header)
        want = next((h for h in heads if h.lower() in q.lower()), None)
        nums = re.findall(r"\(?-?[\d,]+(?:\.\d+)?\)?", row)
        nums = [n for n in nums if n not in ("2025", "2024")]
        if want is None or heads.index(want) >= len(nums):
            return None
        return num(nums[heads.index(want)])
