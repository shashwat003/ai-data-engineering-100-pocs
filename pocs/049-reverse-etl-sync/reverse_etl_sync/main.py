"""POC 049: reverse ETL from DuckDB to a mock CRM; diff-based sync versus naive full refresh."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from pocs_common import write_json
from pocs_common.report import markdown_table

from . import warehouse
from .crm import CRM
from .engine import State, Syncer, read_source

POC_DIR = Path(__file__).resolve().parent.parent


def fresh(n: int = 1000, requests_per_window: int = 3):
    d = tempfile.mkdtemp()
    con = warehouse.build(str(Path(d) / "wh.duckdb"), n)
    crm = CRM(requests_per_window)
    client = TestClient(crm.app)
    return con, crm, client, State()


def row(rep, plan=None) -> dict:
    return {
        "created": rep.created,
        "updated": rep.updated,
        "unchanged_by_crm": rep.unchanged_by_crm,
        "archived": rep.archived,
        "dead_lettered": rep.dead_lettered,
        "api_calls": rep.api_calls,
        "retries": rep.retries,
        "rate_limited": rep.rate_limited,
        "bytes_sent": rep.bytes_sent,
    }


def run() -> dict:
    con, crm, client, state = fresh()
    sync = Syncer(client, state, sleep=crm.advance)
    steps = {}
    _, r = sync.run(read_source(con))
    steps["1. initial load (1000 rows, 3 requests per 10 s window)"] = row(r)
    _, r = sync.run(read_source(con))
    steps["2. nothing changed"] = row(r)
    ch = warehouse.apply_changes(con, 0.05, 10, 20, bad_email_ids=("c00003",))
    plan, r = sync.run(read_source(con))
    steps["3. next day: 5% updated, 10 deleted, 20 new, 1 bad email"] = row(r)
    # an outage in the middle of the next change set
    warehouse.apply_changes(con, 0.05, 0, 0, seed=21)
    crm.fail_next(4, 503)
    _, r = sync.run(read_source(con))
    steps["4. 4 consecutive 503s during sync"] = row(r)
    _, r = sync.run(read_source(con))
    steps["5. re-run after the outage"] = row(r)
    # naive full refresh of everything for comparison
    con2, crm2, client2, state2 = fresh()
    naive = Syncer(client2, state2, sleep=crm2.advance)
    naive.run(read_source(con2))
    state2.db.execute(
        "DELETE FROM synced"
    )  # forget what was synced: a naive job pushes every row again
    state2.db.commit()
    _, r = naive.run(read_source(con2))
    full = row(r)
    cur_state = state.rows()
    src = read_source(con)
    in_sync = sum(
        1
        for e, f in src.items()
        if e in crm.contacts and all(crm.contacts[e].get(k) == v for k, v in f.items())
    )
    pii = {k for c in crm.contacts.values() for k in c}
    # mass-delete protection
    con.execute("DELETE FROM customers_360")
    blocked = False
    try:
        sync.run(read_source(con))
    except Exception as e:  # noqa: BLE001
        blocked = type(e).__name__ == "MassDeleteBlocked"
    return {"steps": steps, "naive_full_refresh_second_run": full, "source_rows": len(src), "crm_in_sync_with_source": in_sync, "crm_fields_ever_sent": sorted(pii), "dead_letters": len(state.dead()), "archived_in_crm": len(crm.archived),
            "mass_delete_blocked": blocked, "changes_applied_on_day2": {k: (len(v) if isinstance(v, list) else v) for k, v in ch.items()}, "crm_calls": crm.calls, "state_active": sum(1 for v in cur_state.values() if v[2] == "active")}  # fmt: skip


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "results.json", r)
    rows = [
        (
            k,
            v["created"],
            v["updated"],
            v["archived"],
            v["dead_lettered"],
            v["api_calls"],
            v["retries"],
            v["rate_limited"],
            v["bytes_sent"],
        )
        for k, v in r["steps"].items()
    ]
    rows.append(
        (
            "naive: push every row again",
            r["naive_full_refresh_second_run"]["created"]
            + r["naive_full_refresh_second_run"]["updated"]
            + r["naive_full_refresh_second_run"]["unchanged_by_crm"],
            "-",
            "-",
            "-",
            r["naive_full_refresh_second_run"]["api_calls"],
            r["naive_full_refresh_second_run"]["retries"],
            r["naive_full_refresh_second_run"]["rate_limited"],
            r["naive_full_refresh_second_run"]["bytes_sent"],
        )
    )
    md = (
        "# Results: reverse ETL from DuckDB to a mock CRM (simulated time, rate limits and outages)\n\n"
        + markdown_table(
            [
                "step",
                "created",
                "updated",
                "archived",
                "dead-lettered",
                "API calls",
                "retries",
                "429s",
                "bytes sent",
            ],
            rows,
        )
        + f"\n\nAfter the steps: {r['crm_in_sync_with_source']} of {r['source_rows']} source rows match the CRM exactly (the rest are the dead-lettered bad email and rows removed). CRM fields ever received: {r['crm_fields_ever_sent']} "
        f"(internal_note and ssn_last4 never leave the warehouse). Archived in CRM: {r['archived_in_crm']}. Emptying the source table is blocked by the mass-delete guard: {r['mass_delete_blocked']}.\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
