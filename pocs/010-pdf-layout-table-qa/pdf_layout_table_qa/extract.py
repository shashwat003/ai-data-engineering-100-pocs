"""PDF extraction: a naive text dump vs a layout-aware pass (columns, repeated header/footer, tables)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pdfplumber

MAX_PAGES = 200
MARGIN = 55  # points trimmed when a line repeats on most pages


@dataclass
class PageContent:
    page: int
    text: str  # prose in reading order, header/footer removed (layout mode)
    tables: list[list[list[str]]] = field(default_factory=list)
    captions: list[str] = field(default_factory=list)


class PdfError(ValueError):
    pass


def open_pdf(path: str):
    try:
        pdf = pdfplumber.open(path)
    except Exception as e:  # pdfminer raises several unrelated types on garbage input
        raise PdfError(f"not a readable PDF: {path} ({type(e).__name__})") from e
    if len(pdf.pages) > MAX_PAGES:
        pdf.close()
        raise PdfError(f"too many pages (> {MAX_PAGES})")
    return pdf


def naive_pages(path: str) -> list[str]:
    with open_pdf(path) as pdf:
        return [p.extract_text() or "" for p in pdf.pages]


def _norm(line: str) -> str:
    return re.sub(r"\d+", "#", line.strip().lower())


def repeated_lines(pdf) -> set[str]:
    """Normalised lines appearing in the top/bottom margin on at least 60% of pages (running headers/footers)."""
    counts: dict[str, int] = {}
    for p in pdf.pages:
        for crop in (
            p.crop((0, 0, p.width, MARGIN)),
            p.crop((0, p.height - MARGIN, p.width, p.height)),
        ):
            for ln in (crop.extract_text() or "").splitlines():
                if ln.strip():
                    counts[_norm(ln)] = counts.get(_norm(ln), 0) + 1
    return {k for k, v in counts.items() if v >= 0.6 * len(pdf.pages)}


def _column_split(page) -> float | None:
    """x of an empty vertical gutter if the body has two columns, else None."""
    words = page.extract_words()
    if len(words) < 30:
        return None
    mid = page.width / 2
    band = [w for w in words if w["x0"] < mid + 6 and w["x1"] > mid - 6]
    return mid if len(band) <= 0.03 * len(words) else None


def layout_pages(path: str) -> list[PageContent]:
    out = []
    with open_pdf(path) as pdf:
        top, bottom = repeated_lines(pdf)
        for i, p in enumerate(pdf.pages, start=1):
            tables = p.find_tables()
            tb = [t.bbox for t in tables]
            body = p.crop((0, MARGIN if top else 0, p.width, p.height - (MARGIN if bottom else 0)))
            for b in tb:
                body = body.outside_bbox(b)
            gutter = _column_split(body)
            if gutter:
                chunks = [
                    body.crop((body.bbox[0], body.bbox[1], gutter, body.bbox[3])).extract_text()
                    or "",
                    body.crop((gutter, body.bbox[1], body.bbox[2], body.bbox[3])).extract_text()
                    or "",
                ]
            else:
                chunks = [body.extract_text() or ""]
            lines = [ln for c in chunks for ln in c.splitlines()]
            caps = [ln for ln in lines if re.match(r"Table \d+\.", ln)]
            prose = [ln for ln in lines if ln not in caps]
            out.append(
                PageContent(
                    i,
                    " ".join(prose),
                    [[[c or "" for c in row] for row in t.extract()] for t in tables],
                    caps,
                )
            )
    return out
