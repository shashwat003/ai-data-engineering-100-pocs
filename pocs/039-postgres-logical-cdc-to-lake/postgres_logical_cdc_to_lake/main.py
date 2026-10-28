"""POC 039: PostgreSQL logical-decoding CDC into a Parquet lake. REAL: PostgreSQL 16 server, WAL logical decoding
(test_decoding), replication slot, DuckDB/Parquet lake. NOT used: Debezium, Kafka, Kafka Connect, pgoutput protocol, cloud."""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

from . import scenarios as S
from .decode import envelope, parse_change
from .pgserver import PgServer, find_bin

POC_DIR = Path(__file__).resolve().parent.parent


def sample_envelope() -> dict:
    ch = parse_change(
        "table public.orders: UPDATE: old-key: id[integer]:7 status[text]:'new' amount[numeric]:10.50 note[text]:null new-tuple: id[integer]:7 status[text]:'paid' amount[numeric]:10.50 note[text]:null"
    )
    e = envelope(ch, lsn=22_000_000, xid=731, ordinal=0, commit_ts="2026-01-01 00:00:00+00")
    return json.loads(json.dumps(e, default=str))


def run(quick: bool = False) -> dict:
    if find_bin() is None:
        raise SystemExit(
            "PostgreSQL server binaries not found (looked in /usr/lib/postgresql/*/bin and PATH)"
        )
    out: dict = {"envelope_example": sample_envelope()}
    plan = {
        "first_load_with_concurrent_writes": S.first_load_with_concurrent_writes,
        "streaming_atomicity": lambda pg: S.streaming_atomicity(pg, txns=150 if quick else 400),
        "crash_and_replay": S.crash_and_replay,
        "schema_evolution": S.schema_evolution,
        "slot_lag": lambda pg: S.slot_lag(pg, txns=100 if quick else 300),
        "unsupported_type_fails_closed": S.unsupported_type_fails_closed,
    }
    for name, fn in plan.items():
        with PgServer() as pg:
            out[name] = fn(pg)
    return out


def _rec(r: dict) -> str:
    return "; ".join(
        f"{t.split('.')[-1]}: {v['source_rows']} src / {v['lake_rows']} lake, {v['missing_in_lake']} missing, {v['extra_in_lake']} extra, {v['differing']} differing"
        for t, v in r.items()
    )


def render(res: dict) -> str:
    a, b, c, d, e, f = (
        res[k]
        for k in (
            "first_load_with_concurrent_writes",
            "streaming_atomicity",
            "crash_and_replay",
            "schema_evolution",
            "slot_lag",
            "unsupported_type_fails_closed",
        )
    )
    se = d
    md = [
        "# Results (POC 039: REAL PostgreSQL 16 logical decoding into a Parquet lake; no Debezium, no Kafka)",
        "Every scenario starts its own throw-away server. Row counts are exact; timings are measured on one machine and noisy.",
        "\n## 1. First load while the source is being written\n",
        f"A snapshot is taken after the slot exists, while {a['background_txns']} concurrent background transactions ran. Source vs lake after draining: {_rec(a['reconcile'])}.",
        "\n## 2. Streaming with transactions and rollbacks\n",
        markdown_table(
            ["check", "result"],
            [
                ["committed / rolled-back transactions", f"{b['committed']} / {b['rolled_back']}"],
                ["polls interleaved with the workload", b["polls"]],
                [
                    "silver total balance != constant after any poll (atomicity violations)",
                    b["atomicity_violations"],
                ],
                [
                    "rows from rolled-back transactions in silver / events in bronze",
                    f"{b['ghost_rows_in_silver']} / {b['ghost_events_in_bronze']}",
                ],
                ["final reconcile", _rec(b["reconcile"])],
            ],
        ),  # fmt: skip
        "\n## 3. Crash after the lake write, before the slot advance\n",
        markdown_table(
            ["check", "result"],
            [
                [
                    "crash injected / slot not advanced",
                    f"{c['crashed']} / {c['slot_not_advanced_by_crash']}",
                ],
                [
                    "bronze changelog events vs distinct events (restart used a different batch size)",
                    f"{c['changelog_events']} vs {c['distinct_events']}",
                ],
                ["final reconcile", _rec(c["reconcile"])],
            ],
        ),  # fmt: skip
        "\n## 4. Schema evolution\n",
        markdown_table(
            ["step", "reconcile"],
            [
                [
                    "ADD COLUMN channel (nullable), 15 rows updated",
                    _rec({"orders": se["nullable_column"]}),
                ],
                [
                    "ADD COLUMN priority DEFAULT 'normal' (no WAL event per row)",
                    _rec({"orders": se["column_with_default_before_repair"]}),
                ],
                [
                    f"after re-snapshot of the table ({se['resnapshot_rows']} rows)",
                    _rec({"orders": se["after_repair"]}),
                ],
            ],
        ),  # fmt: skip
        "\n## 5. Replication-slot lag\n",
        markdown_table(
            ["moment", "WAL bytes the slot is behind"],
            [
                ["idle", e["idle_lag_bytes"]],
                ["after writes, consumer stopped", e["lag_after_writes_bytes"]],
                ["after draining", e["lag_after_drain_bytes"]],
            ],
        ),
        f"\nDrained {e['drained_transactions']} transactions in {e['drain_seconds_measured']} s (measured, noisy). An idle slot retains WAL on the server indefinitely, so lag must be alerted on; the demo server also sets `max_slot_wal_keep_size`.",
        "\n## 6. Unsupported column type (jsonb)\n",
        f"Consumer stopped with `{f['error']}`; slot lag after the failure {f['lag_bytes_after_failure']} bytes (not advanced), bronze files written: {f['bronze_files']}.",
        "\n## Debezium-shaped event produced for an UPDATE\n",
        "```json\n" + json.dumps(res["envelope_example"], indent=1) + "\n```\n",
    ]
    return "\n".join(md)


def main() -> None:
    res = run(quick=bool(os.environ.get("DEMO_QUICK")))
    out = POC_DIR / "results"
    out.mkdir(exist_ok=True)
    write_json(out / "metrics.json", res)
    (out / "results.md").write_text(render(res))
    print(render(res))


if __name__ == "__main__":
    random.seed(0)
    main()
