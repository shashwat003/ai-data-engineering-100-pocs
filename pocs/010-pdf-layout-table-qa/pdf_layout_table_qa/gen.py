"""Synthetic annual-report PDFs (reportlab) with known facts: two-column prose, ruled tables with gaps, repeating header/footer."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import Frame, Paragraph, Table, TableStyle

COMPANIES = ["Alpha Dynamics", "Brightwater Foods", "Cobalt Mobility", "Delta Textiles"]
CITIES = [
    "Berlin",
    "Lyon",
    "Porto",
    "Turin",
    "Graz",
    "Gdansk",
    "Bilbao",
    "Leeds",
    "Ghent",
    "Malmo",
    "Cork",
    "Brno",
]
REGIONS = ["North America", "EMEA", "Asia Pacific", "Latin America"]
QUARTERS = ["Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025"]
FUNCTIONS = ["Engineering", "Sales and marketing", "Operations", "Finance"]
YEARS = ["2024", "2025"]


@dataclass
class Fact:
    kind: str  # text_open | text_staff | table_cell
    company: str
    question: str
    answer: str
    page: int


@dataclass
class DocSpec:
    company: str
    path: Path
    facts: list[Fact] = field(default_factory=list)


def _fmt(v: float | None) -> str:
    if v is None:
        return ""
    return f"({abs(v):,.1f})" if v < 0 else f"{v:,.1f}"


def make_doc(company: str, path: Path, seed: int) -> DocSpec:
    rng = random.Random(seed)
    spec = DocSpec(company, path)
    cv = canvas.Canvas(str(path), pagesize=A4)
    W, H = A4
    styles = getSampleStyleSheet()
    body = styles["BodyText"]

    def chrome(page_no: int):
        cv.setFont("Helvetica", 8)
        cv.drawString(50, H - 30, f"{company} | Annual Report 2025 | Confidential")
        cv.drawString(W / 2 - 25, 25, f"Page {page_no} of 4")

    def two_columns(sentences: list[str]):
        left = sentences[: len(sentences) // 2]
        right = sentences[len(sentences) // 2 :]
        for x, sents in ((50, left), (W / 2 + 10, right)):
            Frame(x, 60, W / 2 - 70, H - 140, showBoundary=0).addFromList(
                [Paragraph(" ".join(sents), body)], cv
            )

    cities = rng.sample(CITIES, 6)
    sentences = []
    for i, city in enumerate(cities):
        year, staff = rng.randint(1995, 2023), rng.randint(80, 950)
        sentences.append(
            f"The {city} plant opened in {year} and employs {staff} people across three shifts. Local sourcing at {city} reduced transport emissions during the reporting period."
        )
        spec.facts.append(
            Fact(
                "text_open",
                company,
                f"In which year did the {city} plant of {company} open?",
                str(year),
                1 if i < 4 else 3,
            )
        )
        spec.facts.append(
            Fact(
                "text_staff",
                company,
                f"How many people does the {city} plant of {company} employ?",
                str(staff),
                1 if i < 4 else 3,
            )
        )
    # page 1 and 3: two-column prose
    chrome(1)
    cv.setFont("Helvetica-Bold", 14)
    cv.drawString(50, H - 60, "Operations review")
    two_columns(sentences[:4])
    cv.showPage()
    # page 2: revenue table (with gaps and negatives)
    chrome(2)
    cv.setFont("Helvetica-Bold", 11)
    cv.drawString(50, H - 70, "Table 1. Net revenue by region (USD m)")
    data = [["Region", *QUARTERS]]
    for reg in REGIONS:
        row = [reg]
        for q in QUARTERS:
            v = (
                None
                if rng.random() < 0.15
                else round(rng.uniform(-30, 900), 1)
                if rng.random() < 0.1
                else round(rng.uniform(40, 900), 1)
            )
            row.append(_fmt(v))
            if v is not None:
                spec.facts.append(
                    Fact(
                        "table_cell",
                        company,
                        f"What was net revenue for {reg} in {q} (USD m) at {company}?",
                        f"{v:.1f}",
                        2,
                    )
                )
            else:
                spec.facts.append(
                    Fact(
                        "table_gap",
                        company,
                        f"What was net revenue for {reg} in {q} (USD m) at {company}?",
                        "",
                        2,
                    )
                )
        data.append(row)
    _table(cv, data, 50, H - 90 - 25 * len(data), [130, 80, 80, 80, 80])
    cv.showPage()
    # page 3: more prose + headcount table
    chrome(3)
    cv.setFont("Helvetica-Bold", 14)
    cv.drawString(50, H - 60, "People and sites")
    two_columns(sentences[4:] + ["Training hours per employee rose again."])
    cv.setFont("Helvetica-Bold", 11)
    cv.drawString(50, 330, "Table 2. Headcount by function")
    hc = [["Function", *YEARS]]
    for fn in FUNCTIONS:
        row = [fn]
        for y in YEARS:
            v = rng.randint(40, 3000)
            row.append(f"{v:,}")
            spec.facts.append(
                Fact(
                    "table_cell",
                    company,
                    f"What was headcount for {fn} in {y} at {company}?",
                    str(v),
                    3,
                )
            )
        hc.append(row)
    _table(cv, hc, 50, 330 - 25 * len(hc) - 10, [160, 90, 90])
    cv.showPage()
    chrome(4)
    cv.setFont("Helvetica", 10)
    cv.drawString(50, H - 80, "Notes: figures are unaudited. Values in parentheses are negative.")
    cv.showPage()
    cv.save()
    return spec


def _table(cv, data, x, y, widths):
    t = Table(data, colWidths=widths, rowHeights=25)
    t.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ]
        )
    )
    t.wrapOn(cv, 0, 0)
    t.drawOn(cv, x, y)


def make_corpus(out_dir: str | Path, seed: int = 3) -> list[DocSpec]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    return [
        make_doc(c, out / f"{c.split()[0].lower()}.pdf", seed * 100 + i)
        for i, c in enumerate(COMPANIES)
    ]
