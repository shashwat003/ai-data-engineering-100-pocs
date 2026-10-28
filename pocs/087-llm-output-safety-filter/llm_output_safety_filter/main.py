"""POC 087: layered LLM output safety filter evaluated on clean unsafe outputs, evasions and benign look-alikes."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .evaluate import run

POC_DIR = Path(__file__).resolve().parent.parent


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "results.json", r)
    md = (
        "# Results: layered output safety filter on synthetic outputs\n\n"
        f"Classifier (TF-IDF word+char n-grams, logistic regression, trained on synthetic text; test templates and insult words never seen in training): accuracy {r['classifier']['accuracy']} on {r['classifier']['n']} texts; per class {r['classifier']['per_class']}.\n\n"
        "Share of unsafe outputs caught (decision other than allow), clean wording:\n\n"
        + markdown_table(["category", "caught"], list(r["recall_clean"].items()))
        + f"\n\nBenign look-alikes (security discussion, 'kill the process', allowed links and commands) blocked, escalated or altered: {r['benign_blocked_or_changed']} ({r['benign_false_positives']}).\n\n"
        "Evasion attempts on toxic, self-harm, weapon, command and prompt-leak texts:\n\n"
        + markdown_table(
            ["transformation", "caught with normaliser", "caught without"],
            [(k, v["with_normaliser"], v["without"]) for k, v in r["evasion"].items()],
        )
        + f"\n\nAblation (share of all unsafe outputs still caught with one layer removed; all layers: {r['recall_all_layers']}):\n\n"
        + markdown_table(["layer removed", "caught"], list(r["recall_without_layer"].items()))
        + f"\n\nLatency per output: p50 {r['latency_ms']['p50']} ms, p95 {r['latency_ms']['p95']} ms on one CPU core.\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
