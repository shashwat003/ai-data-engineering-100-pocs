"""POC 032: resilient vs naive browser extraction against a deliberately hostile local site."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .extract import extract
from .site import Site, valid_ids

POC_DIR = Path(__file__).resolve().parent.parent


def run() -> dict:
    truth = valid_ids()
    out = {"expected_valid_records": len(truth)}
    for mode in ("naive", "resilient"):
        with Site() as site:
            r = extract(site.url, resilient=mode == "resilient")
            ids = {x.id for x in r.records}
            out[mode] = {"records": len(ids), "recall": round(len(ids & truth) / len(truth), 3), "false_records": len(ids - truth), "quarantined": len(r.quarantined),
                         "pages_ok": r.pages_ok, "pages_failed": r.pages_failed, "retries": r.retries, "selector_fallbacks": r.selector_fallbacks, "seconds": r.seconds}  # fmt: skip
            out[mode]["quarantine_reasons"] = sorted({q["error"] for q in r.quarantined})
    return out


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    rows = [
        [
            m,
            r[m]["records"],
            r[m]["recall"],
            r[m]["false_records"],
            r[m]["quarantined"],
            r[m]["pages_ok"],
            r[m]["retries"],
            r[m]["selector_fallbacks"],
            r[m]["seconds"],
        ]
        for m in ("naive", "resilient")
    ]
    md = ["# Results (measured by `python -m playwright_web_extractor.main`; local hostile test site, real Chromium)", "", f"Ground truth: {r['expected_valid_records']} valid records of 40 rows (3 invalid + 1 duplicate id).", "",
          markdown_table(["extractor", "records", "recall", "false records", "quarantined", "pages ok", "retries", "selector fallbacks", "seconds"], rows), "",
          f"- resilient quarantine reasons: {r['resilient']['quarantine_reasons']}"]  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
