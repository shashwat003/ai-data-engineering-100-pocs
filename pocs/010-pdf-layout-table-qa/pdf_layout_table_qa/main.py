"""POC 010: layout-aware vs naive PDF extraction, scored by exact-match QA on synthetic reports."""

from __future__ import annotations

import tempfile
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .extract import layout_pages, naive_pages
from .gen import make_corpus
from .qa import FlatTableQA, TableQA, TextQA, company_of

POC_DIR = Path(__file__).resolve().parent.parent


def evaluate(specs) -> dict:
    stats: dict = {}
    noise = {"naive": 0, "layout": 0}
    with_cite = 0
    table_answers = 0
    for spec in specs:
        flat = naive_pages(str(spec.path))
        lay = layout_pages(str(spec.path))
        text_naive, text_layout = TextQA(flat), TextQA([p.text for p in lay])
        t_layout, t_flat = TableQA(lay), FlatTableQA(flat)
        noise["naive"] += sum("Confidential" in t for t in flat)
        noise["layout"] += sum("Confidential" in p.text for p in lay)
        for f in spec.facts:
            assert company_of(f.question) == spec.company
            if f.kind.startswith("text"):
                got = {
                    "naive": text_naive.answer(f.question),
                    "layout": text_layout.answer(f.question),
                }
            else:
                lv, cite = t_layout.answer(f.question)
                got = {"naive": t_flat.answer(f.question), "layout": lv}
                if f.kind == "table_cell" and lv == f.answer:
                    with_cite += cite == f"p{f.page}"
                    table_answers += 1
            for mode, ans in got.items():
                s = stats.setdefault(f.kind, {}).setdefault(mode, [0, 0])
                s[1] += 1
                s[0] += (ans or "") == f.answer
    return {"scores": {k: {m: {"correct": c, "n": n, "accuracy": round(c / n, 3)} for m, (c, n) in v.items()} for k, v in stats.items()},
            "pages_with_running_header_in_extracted_text": noise, "table_answers_with_correct_page_citation": f"{with_cite}/{table_answers}"}  # fmt: skip


def run() -> dict:
    with tempfile.TemporaryDirectory() as t:
        return evaluate(make_corpus(t))


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    rows = [
        [k, m, v["correct"], v["n"], v["accuracy"]]
        for k, d in r["scores"].items()
        for m, v in d.items()
    ]
    md = ["# Results (measured by `python -m multimodal_doc_qa.main`; 4 synthetic reports x 4 pages, reportlab PDFs, exact match)", "", markdown_table(["question kind", "extraction", "correct", "n", "accuracy"], rows), "",
          f"- pages whose extracted text still contains the running header: {r['pages_with_running_header_in_extracted_text']}", f"- correct table answers carrying the right page citation: {r['table_answers_with_correct_page_citation']}",
          "- `table_gap` = questions whose cell is empty in the PDF; the correct answer is *no answer*."]  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
