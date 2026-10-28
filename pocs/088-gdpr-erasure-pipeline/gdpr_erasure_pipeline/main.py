"""POC 088: GDPR erasure across a Delta Lake table and an encrypted vector store, with verification."""

from __future__ import annotations

import shutil
import tempfile
from datetime import date
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from . import lake, synth
from .erasure import Pipeline, apply_retention
from .vectors import KeyStore, TicketStore, file_residue

POC_DIR = Path(__file__).resolve().parent.parent
TODAY = synth.TODAY


def setup(root: Path, n_people: int = 30) -> tuple[Pipeline, list[dict], list[dict]]:
    ppl = synth.people(n_people)
    ev = synth.events(ppl)
    lake.create(root / "lake" / "events", ev)
    keys = KeyStore(root / "keys.json")  # lives outside the data and backup directories
    ts = TicketStore(root / "tickets", keys)
    for t in synth.tickets(ppl):
        ts.add(t["ticket_id"], t["subject"], t["text"])
    return (
        Pipeline(root / "state", root / "lake" / "events", ts, salt=b"demo-salt-0123456789"),
        ppl,
        ev,
    )


def run() -> dict:
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        pipe, ppl, ev = setup(root)
        victim, other = ppl[3], ppl[4]
        ids = [victim["email"], victim["name"]]
        before = {"lake_rows": lake.count(pipe.lake_path, victim["user_id"]), "tickets": pipe.tickets.count(victim["user_id"]), "old_versions_with_subject": lake.time_travel_exposes(pipe.lake_path, victim["user_id"]),
                  "residue_files_before": len(lake.residue(pipe.lake_path, ids)) + len(file_residue(pipe.tickets.dir, ids))}  # fmt: skip
        backup = pipe.tickets.snapshot(root / "backup_tickets")
        lake_backup = root / "backup_lake"
        shutil.copytree(pipe.lake_path, lake_backup)
        # naive erasure: DELETE without VACUUM leaves the data in old files and old versions
        naive_root = root / "naive"
        lake.create(naive_root, ev)
        lake.erase_subject(naive_root, other["user_id"], vacuum=False)
        naive = {
            "rows_visible": lake.count(naive_root, other["user_id"]),
            "residue_hits": len(lake.residue(naive_root, [other["email"]])),
            "old_versions_exposing": lake.time_travel_exposes(naive_root, other["user_id"]),
        }
        # proper erasure with a crash after the lake step, then resume
        try:
            pipe.erase(victim["user_id"], TODAY, ids, fail_after_lake=True)
        except RuntimeError:
            pass
        res = pipe.erase(victim["user_id"], TODAY, ids)
        again = pipe.erase(victim["user_id"], TODAY, ids)
        after = {
            "status": res["status"],
            "residue_hits": res["residue_hits"],
            "readable_old_versions": res["readable_old_versions"],
            "lake_rows_left": res["lake_rows_left"],
            "tickets_left": res["tickets_left"],
        }
        collateral = {"other_events_kept": lake.count(pipe.lake_path, other["user_id"]) > 0, "other_tickets_kept": pipe.tickets.count(other["user_id"]) == 2,
                      "search_still_works": len(pipe.tickets.search("duplicate charge invoice", 3)) == 3}  # fmt: skip
        backups = {"ticket_backup_readable_for_subject": TicketStore.readable_in(backup, pipe.tickets.keys, victim["user_id"]), "ticket_backup_plaintext_hits": len(file_residue(backup, ids)),
                   "lake_backup_still_has_subject": lake.count(lake_backup, victim["user_id"]) > 0}  # fmt: skip
        hold_pipe = Pipeline(
            root / "state2",
            pipe.lake_path,
            pipe.tickets,
            salt=b"demo-salt-0123456789",
            holds={ppl[5]["user_id"]},
        )
        held = hold_pipe.erase(ppl[5]["user_id"], TODAY)
        ret = apply_retention(pipe.lake_path, TODAY, 90, holds={ppl[6]["user_id"]})
        old_rows_left = [
            r
            for r in __import__("deltalake")
            .DeltaTable(str(pipe.lake_path))
            .to_pyarrow_table()
            .to_pylist()
            if r["event_date"] < ret["cutoff"] and r["user_id"] != ppl[6]["user_id"]
        ]
        return {"before": before, "naive_delete_only": naive, "after_erasure": after, "resumed_after_crash": res["steps"].get("lake", {}).get("rows_deleted"), "second_request": again["status"], "collateral": collateral, "backups": backups,
                "legal_hold": held["status"], "retention": {**ret, "expired_rows_left_without_hold": len(old_rows_left)}, "audit_chain_valid": pipe.verify_audit_chain(), "overdue_requests": pipe.overdue(date(2025, 8, 1))}  # fmt: skip


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "results.json", r)
    rows = [
        (
            "subject rows in lake / tickets before",
            f"{r['before']['lake_rows']} / {r['before']['tickets']}",
        ),
        (
            "old table versions that expose the subject before",
            r["before"]["old_versions_with_subject"],
        ),
        (
            "DELETE only (no VACUUM): rows visible, files still containing the subject, old versions exposing",
            f"{r['naive_delete_only']['rows_visible']}, {r['naive_delete_only']['residue_hits']}, {r['naive_delete_only']['old_versions_exposing']}",
        ),
        ("full erasure after a simulated crash and resume: status", r["after_erasure"]["status"]),
        (
            "residue hits / readable old versions / rows left / tickets left",
            f"{r['after_erasure']['residue_hits']} / {r['after_erasure']['readable_old_versions']} / {r['after_erasure']['lake_rows_left']} / {r['after_erasure']['tickets_left']}",
        ),
        ("second identical request", r["second_request"]),
        ("other subjects untouched, search still works", r["collateral"]),
        (
            "ticket backup: tickets still decryptable / plaintext hits",
            f"{r['backups']['ticket_backup_readable_for_subject']} / {r['backups']['ticket_backup_plaintext_hits']}",
        ),
        (
            "lake backup still contains the subject (unencrypted)",
            r["backups"]["lake_backup_still_has_subject"],
        ),
        ("legal hold request", r["legal_hold"]),
        (
            "retention (90 days, one subject on hold): rows deleted, expired rows left",
            f"{r['retention']['rows_deleted']}, {r['retention']['expired_rows_left_without_hold']}",
        ),
        (
            "audit hash chain valid / overdue requests on 2025-08-01",
            f"{r['audit_chain_valid']} / {r['overdue_requests']}",
        ),
    ]
    md = (
        "# Results: erasure across Delta Lake and an encrypted vector store (synthetic people)\n\n"
        + markdown_table(["check", "result"], rows)
        + "\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
