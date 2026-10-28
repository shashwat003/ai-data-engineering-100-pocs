"""POC 080: classify an AI system inventory against simplified EU AI Act rules and render a register."""

from __future__ import annotations

from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .classify import classify, load_inventory, summarize
from .render import render

POC_DIR = Path(__file__).resolve().parent.parent


def run(path: str | Path | None = None) -> dict:
    items = [classify(s) for s in load_inventory(path or POC_DIR / "inventory" / "systems.yaml")]
    summary = summarize(items)
    return {"items": items, "summary": summary, "register": render(items, summary)}


def main() -> None:
    res = run()
    (POC_DIR / "results").mkdir(exist_ok=True)
    (POC_DIR / "results" / "register.md").write_text(res["register"])
    write_json(
        POC_DIR / "results" / "results.json", {"items": res["items"], "summary": res["summary"]}
    )
    rows = [(i["id"], i["name"], i["tier"], ", ".join(i["gaps"]) or "-") for i in res["items"]]
    md = (
        "# Results: AI risk register for a synthetic 7-system inventory\n\n"
        f"Summary: {res['summary']}. Full register: `results/register.md`.\n\n"
        + markdown_table(["id", "system", "tier", "missing evidence"], rows)
        + "\n\nTiers come from a deliberately small rule set (3 prohibited practices, 6 Annex III areas, "
        "transparency duties); a human must confirm every tier.\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
