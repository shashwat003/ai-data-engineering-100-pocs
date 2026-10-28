"""POC 004: schema-validated extraction with error-feedback retries, measured on 200 synthetic invoices."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .docs import make_docs
from .pipeline import extract
from .simmodel import SimExtractor

POC_DIR = Path(__file__).resolve().parent.parent
FIELDS = ["vendor", "invoice_number", "invoice_date", "currency", "subtotal", "tax", "total"]


def accuracy(data: dict, truth: dict) -> float:
    ok = sum(data[f] == truth[f] for f in FIELDS)
    items = [(x["description"], x["qty"], x["unit_price"]) for x in data["line_items"]] == [
        (x["description"], x["qty"], x["unit_price"]) for x in truth["line_items"]
    ]
    return (ok + items) / (len(FIELDS) + 1)


def evaluate(docs, max_attempts: int, fault_rate=0.55, repair_prob=0.8) -> dict:
    client = SimExtractor(fault_rate=fault_rate, repair_prob=repair_prob, seed=1)
    rs = [extract(d["id"], d["text"], client, max_attempts) for d in docs]
    ok = [(r, d) for r, d in zip(rs, docs, strict=True) if r.status == "ok"]
    exact = sum(accuracy(r.data, d["truth"]) == 1.0 for r, d in ok)
    inj = [(r, d) for r, d in ok if d["injected"]]
    return {
        "max_attempts": max_attempts,
        "valid_rate": round(len(ok) / len(docs), 4),
        "dead_letter": len(docs) - len(ok),
        "exact_match_of_accepted": round(exact / max(1, len(ok)), 4),
        "mean_attempts": round(sum(r.attempts for r in rs) / len(rs), 3),
        "tokens_in_total": sum(r.tokens_in for r in rs),
        "injected_docs_accepted": len(inj),
        "injected_total_zero": sum(r.data["total"] == 0 for r, _ in inj),
    }


def run(n: int = 200) -> dict:
    docs = make_docs(n)
    return {"n_docs": n, "n_injected": sum(d["injected"] for d in docs),
            "sweep": [evaluate(docs, k) for k in (1, 2, 3, 5)]}  # fmt: skip


def main() -> None:
    res = run()
    write_json(POC_DIR / "results" / "results.json", res)
    rows = [(s["max_attempts"], s["valid_rate"], s["dead_letter"], s["exact_match_of_accepted"], s["mean_attempts"], s["tokens_in_total"], s["injected_total_zero"]) for s in res["sweep"]]  # fmt: skip
    md = (
        f"# Results: schema-validated extraction on {res['n_docs']} synthetic invoices "
        f"({res['n_injected']} contain a prompt-injection line)\n\n"
        "The extractor is a SIMULATED model (regex reader with seeded, realistic format faults: fenced or "
        "trailing-comma JSON, wrong date format, string amounts, wrong total, missing field, bad currency, "
        "chatty prose). 55% of first attempts are faulty; with validation feedback a fault is repaired with an "
        "assumed 80% probability per retry. The repair rate for a real model is not measured.\n\n"
        + markdown_table(
            [
                "max attempts",
                "valid rate",
                "dead-lettered",
                "exact match (accepted)",
                "mean attempts",
                "input tokens (est.)",
                "injected docs with total=0",
            ],
            rows,
        )
        + "\n\nAccepted records are always schema-valid and arithmetically consistent; documents that never "
        "validate are dead-lettered, never passed on.\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
