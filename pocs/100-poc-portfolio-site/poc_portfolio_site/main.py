"""POC 100: build the portfolio site for THIS repository and report the repo-wide quality check."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json

from .model import MATURITY, check_repo, load_repo
from .site import build_site

POC_DIR = Path(__file__).resolve().parent.parent
REPO = POC_DIR.parent.parent


def run(out_dir: Path | None = None) -> dict:
    pocs = load_repo(REPO)
    out = out_dir or POC_DIR / "results" / "site"
    summary = build_site(pocs, out)
    problems = check_repo(pocs)
    flagged = {
        lab: sum(lab in p.labels for p in pocs)
        for lab in ("simulated", "mock", "modelled", "unverified")
    }
    return {"summary": summary, "check_problems": problems, "label_counts": flagged,
            "maturity": {MATURITY[p.maturity]: sum(MATURITY[q.maturity] == MATURITY[p.maturity] for q in pocs) for p in pocs}}  # fmt: skip


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    md = ["# Results (measured by `python -m poc_portfolio_site.main` against this repository)", "", f"- site pages generated: {r['summary']['pages']} ({r['summary']['built']} POC pages + index) for {r['summary']['total']} catalogue entries",
          f"- maturity: {r['maturity']}", f"- POCs whose evidence table declares simulated/mock/modelled/unverified parts: {r['label_counts']}", f"- repo consistency problems: {len(r['check_problems'])}"] + [f"  - {p}" for p in r["check_problems"][:20]]  # fmt: skip
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
