"""POC 031: scheduled, idempotent, number-verified revenue reports."""

from __future__ import annotations

import tempfile
from datetime import date, datetime
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from .cron import Cron
from .facts import build_facts
from .metrics import INCIDENT_DAY, daily
from .narrative import SimWriter, render
from .scheduler import FileOutbox, Ledger, build_report, run_due
from .verify import check

POC_DIR = Path(__file__).resolve().parent.parent


class FlakySink:
    def __init__(self, inner, fail_first: int):
        self.inner, self.left, self.calls = inner, fail_first, 0

    def send(self, key, subject, body):
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            raise ConnectionError("smtp down")
        self.inner.send(key, subject, body)


def run() -> dict:
    rows = daily()
    end = date(2025, 1, 1).fromordinal(
        date(2025, 1, 1).toordinal() + INCIDENT_DAY + 2
    )  # last day of the incident
    facts = build_facts(rows, end)
    template = render(facts)
    writers = {
        m: build_report(rows, end, SimWriter(m)) for m in ("faithful", "hallucinating", "linking")
    }
    with tempfile.TemporaryDirectory() as t:
        outbox = FileOutbox(t)
        ledger = Ledger()
        cron = Cron("0 8 * * 1")  # Mondays 08:00
        sink = FlakySink(outbox, fail_first=1)
        first = run_due(
            "weekly-revenue",
            cron,
            datetime(2025, 4, 28, 9, 0),
            datetime(2025, 4, 6, 9, 0),
            rows,
            sink,
            ledger,
        )  # 3 Mondays due: catch-up
        again = run_due(
            "weekly-revenue",
            cron,
            datetime(2025, 4, 28, 9, 0),
            datetime(2025, 4, 6, 9, 0),
            rows,
            sink,
            ledger,
        )  # restart, same window
        sent = outbox.sent()
    return {"facts": facts, "template": template, "template_check": check(template, facts), "anomaly_in_report": any(d for d, _ in facts["anomalies"]),
            "writers": {m: {"source": s, "ok": c["ok"], "ungrounded": c["ungrounded_numbers"], "links": len(c["links"])} for m, (_t, s, c) in writers.items()},
            "first_run": first, "second_run": again, "files_in_outbox": len(sent), "distinct_keys": len(set(sent))}  # fmt: skip


def main() -> None:
    res = run()
    write_json(POC_DIR / "results" / "results.json", res)
    md = (
        "# Results: scheduled, idempotent, number-verified reports (synthetic metrics; writer is SIMULATED, no LLM)\n\n"
        f"Report for the incident week:\n\n```\n{res['template']}\n```\n\n"
        + markdown_table(
            ["writer behaviour", "text used", "passed verification", "ungrounded numbers", "links"],
            [
                (m, w["source"], w["ok"], w["ungrounded"] or "-", w["links"])
                for m, w in res["writers"].items()
            ],
        )
        + f"\n\nSchedule `0 8 * * 1`, four Mondays missed since the last tick, first sink call fails: {[(r['period'], r['action']) for r in res['first_run']]}. "
        f"Restart over the same window: {[(r['period'], r['action']) for r in res['second_run']]}. Files in outbox: {res['files_in_outbox']} (distinct idempotency keys {res['distinct_keys']}).\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
