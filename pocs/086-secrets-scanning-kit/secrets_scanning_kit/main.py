"""POC 086: secrets scanning kit; compare the dependency-free scanner with gitleaks on a run-time corpus."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .evaluate import run

POC_DIR = Path(__file__).resolve().parent.parent


def main() -> None:
    res = run()
    write_json(POC_DIR / "results" / "results.json", res)
    rows = [
        (
            n,
            t["recall"],
            t["holdout_recall"],
            ", ".join(t["holdout_missed"]) or "-",
            t["fp_rate"],
            ", ".join(t["missed"]) or "-",
            t["seconds"],
        )
        for n, t in res["tools"].items()
    ]
    md = (
        f"# Results: {res['positives']} planted secrets (tuning corpus), {res['holdout']} hold-out secrets in formats not used for tuning, {res['negatives']} look-alike negatives; all generated at run time\n\n"
        + markdown_table(
            [
                "tool",
                "recall (tuning corpus)",
                "recall (hold-out formats)",
                "hold-out missed",
                "false-positive rate",
                "tuning-corpus missed",
                "seconds",
            ],
            rows,
        )
        + "\n\nBoth tools see the same files. gitleaks rows appear only when the binary is installed (pinned release in CI). "
        "The kit scanner was developed against the tuning corpus, so its score there is optimistic; the hold-out column is the fairer comparison. gitleaks was not tuned on either. Corpora this small are a smoke test, not a benchmark.\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
