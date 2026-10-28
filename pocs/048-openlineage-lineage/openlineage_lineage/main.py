"""POC 048: official OpenLineage client -> local receiver -> lineage graph queries (Marquez NOT used)."""

from __future__ import annotations

import threading
from pathlib import Path

from pocs_common import write_json

from .pipeline import NS, client_for, run_pipeline
from .receiver import make_server
from .store import LineageStore

POC_DIR = Path(__file__).resolve().parent.parent


def run() -> dict:
    store = LineageStore()
    srv = make_server(store)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        c = client_for(url)
        run_pipeline(c)
        k = lambda n: f"{NS}:{n}"  # noqa: E731
        res = {
            "events_accepted": len(store.seen), "rejected": store.rejected,
            "upstream_of_mart_revenue": sorted(store.upstream(k("mart_revenue"))),
            "downstream_of_raw_customers": sorted(store.downstream(k("raw_customers"))),
            "sources": sorted(store.sources()),
            "column_upstream_mart_revenue.revenue": sorted(store.column_upstream(k("mart_revenue") + ".revenue")),
            "column_upstream_mart_revenue.country": sorted(store.column_upstream(k("mart_revenue") + ".country")),
        }  # fmt: skip
        # second run: stg_orders fails; downstream data is stale; third: a job hangs
        run_pipeline(c, fail="stg_orders")
        res["stale_after_failure"] = {
            a.split(":")[1]: b.split(":")[1] for a, b in store.stale_datasets().items()
        }
        run_pipeline(c, hang="stg_customers", skip=("stg_orders",))
        res["stuck_runs"] = [j.split(":")[1] for j in store.stuck_runs()]
    finally:
        srv.shutdown()
        srv.server_close()
    return res


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    md = [
        "# Results (measured by `python -m openlineage_lineage.main`; official openlineage-python client -> local receiver, Marquez not used)",
        "",
    ]
    md += [f"- **{k}**: {v}" for k, v in r.items()]
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
