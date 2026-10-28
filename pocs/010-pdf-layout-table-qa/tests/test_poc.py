import pytest
from pdf_layout_table_qa.extract import PdfError, layout_pages, naive_pages
from pdf_layout_table_qa.gen import make_corpus, make_doc
from pdf_layout_table_qa.main import run
from pdf_layout_table_qa.qa import FlatTableQA, TableQA, TextQA, company_of, num


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    return make_corpus(tmp_path_factory.mktemp("pdfs"))


def test_generation_is_deterministic_and_has_ground_truth(tmp_path):
    a, b = (
        make_doc("Alpha Dynamics", tmp_path / "a.pdf", 5),
        make_doc("Alpha Dynamics", tmp_path / "b.pdf", 5),
    )
    assert [(f.question, f.answer) for f in a.facts] == [(f.question, f.answer) for f in b.facts]
    kinds = {f.kind for f in a.facts}
    assert {"text_open", "text_staff", "table_cell"} <= kinds
    assert len({f.question for f in a.facts}) == len(a.facts)  # no ambiguous duplicate questions


def test_layout_removes_running_header_and_footer(corpus):
    pages = layout_pages(str(corpus[0].path))
    assert len(pages) == 4 and all(
        "Confidential" not in p.text and "Page " not in p.text for p in pages
    )
    assert any("Confidential" in t for t in naive_pages(str(corpus[0].path)))


def test_layout_finds_tables_and_keeps_gaps(corpus):
    pages = layout_pages(str(corpus[0].path))
    assert len(pages[1].tables) == 1 and pages[1].captions[0].startswith("Table 1.")
    t = pages[1].tables[0]
    assert t[0] == ["Region", "Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025"] and all(
        len(r) == 5 for r in t
    )
    assert any(
        "" in r[1:]
        for spec in corpus
        for p in layout_pages(str(spec.path))
        for tb in p.tables
        for r in tb
    )  # empty cells survive


def test_two_column_reading_order_restored(corpus):
    naive = " ".join(naive_pages(str(corpus[0].path))[0].split())
    lay = " ".join(layout_pages(str(corpus[0].path))[0].text.split())
    sentence = [f for f in corpus[0].facts if f.kind == "text_open"][0].question.split()[
        5
    ]  # city name
    assert f"The {sentence} plant opened in" in lay
    assert f"The {sentence} plant opened in" not in naive or lay != naive


def test_num_parsing():
    assert (
        num("1,204.5") == "1204.5"
        and num("(12.5)") == "-12.5"
        and num("") is None
        and num("n/a") is None
    )


def test_company_routing_requires_exactly_one():
    assert company_of("revenue at Alpha Dynamics?") == "Alpha Dynamics"
    assert (
        company_of("compare Alpha Dynamics and Delta Textiles") is None
        and company_of("no company") is None
    )


def test_table_qa_refuses_unknown_row_and_empty_cell(corpus):
    spec = corpus[0]
    qa = TableQA(layout_pages(str(spec.path)))
    gap = next(f for f in spec.facts if f.kind == "table_gap")
    assert qa.answer(gap.question)[0] is None
    assert qa.answer(f"What was net revenue for Atlantis in Q1 2025 at {spec.company}?")[0] is None
    assert qa.answer("Tell me a joke")[0] is None


def test_text_qa_unknown_entity_returns_none(corpus):
    assert (
        TextQA([p.text for p in layout_pages(str(corpus[0].path))]).answer(
            "How many people does the Narnia plant employ?"
        )
        is None
    )


def test_flat_baseline_is_misled_by_gaps(corpus):
    wrong = 0
    for spec in corpus:
        flat = FlatTableQA(naive_pages(str(spec.path)))
        wrong += sum(
            1
            for f in spec.facts
            if f.kind == "table_gap" and flat.answer(f.question) not in (None, "")
        )
    assert wrong >= 5  # whitespace-split alignment hallucinates a value for an empty cell


def test_bad_inputs(tmp_path):
    junk = tmp_path / "x.pdf"
    junk.write_bytes(b"not a pdf")
    with pytest.raises(PdfError):
        naive_pages(str(junk))
    with pytest.raises(PdfError):
        layout_pages(str(tmp_path / "missing.pdf"))


def test_end_to_end_scores():
    r = run()["scores"]
    assert (
        r["table_cell"]["layout"]["accuracy"] == 1.0
        and r["text_staff"]["layout"]["accuracy"] == 1.0
    )
    assert r["table_cell"]["naive"]["accuracy"] < r["table_cell"]["layout"]["accuracy"]
    assert r["text_open"]["naive"]["accuracy"] < 0.5 and r["table_gap"]["naive"]["accuracy"] < 0.5
