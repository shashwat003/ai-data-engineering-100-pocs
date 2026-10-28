"""A small instrumented pipeline that emits real OpenLineage events through the official Python client."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from openlineage.client import OpenLineageClient
from openlineage.client.event_v2 import InputDataset, Job, OutputDataset, Run, RunEvent, RunState
from openlineage.client.facet_v2 import column_lineage_dataset as cl
from openlineage.client.transport.http import HttpConfig, HttpTransport

NS = "warehouse"
PRODUCER = (
    "https://github.com/shashwat003/100-real-world-ai-data-engineering-pocs/tree/main/pocs/048"
)
# job -> (inputs, output, column lineage {out_col: [(input_ds, input_col)]})
JOBS = {
    "stg_orders": (
        ["raw_orders"],
        "stg_orders",
        {"order_id": [("raw_orders", "id")], "amount": [("raw_orders", "amount_cents")]},
    ),
    "stg_customers": (
        ["raw_customers"],
        "stg_customers",
        {"customer_id": [("raw_customers", "id")], "country": [("raw_customers", "country_code")]},
    ),
    "fct_orders": (
        ["stg_orders", "stg_customers"],
        "fct_orders",
        {
            "order_id": [("stg_orders", "order_id")],
            "amount": [("stg_orders", "amount")],
            "country": [("stg_customers", "country")],
        },
    ),
    "mart_revenue": (
        ["fct_orders"],
        "mart_revenue",
        {"revenue": [("fct_orders", "amount")], "country": [("fct_orders", "country")]},
    ),
}


def client_for(url: str) -> OpenLineageClient:
    return OpenLineageClient(
        transport=HttpTransport(
            HttpConfig(
                url=url,
                timeout=3.0,
                retry={
                    "total": 1,
                    "backoff_factor": 0.1,
                    "status_forcelist": [502, 503],
                    "allowed_methods": ["POST"],
                },
            )
        )
    )


def _event(job: str, run_id: str, state: RunState, with_io: bool) -> RunEvent:
    ins, out, cols = JOBS[job]
    outputs = []
    if with_io:
        fields = {
            c: cl.Fields(
                inputFields=[cl.InputField(namespace=NS, name=d, field=f) for d, f in srcs]
            )
            for c, srcs in cols.items()
        }
        outputs = [
            OutputDataset(
                namespace=NS,
                name=out,
                facets={"columnLineage": cl.ColumnLineageDatasetFacet(fields=fields)},
            )
        ]
    return RunEvent(eventTime=datetime.now(UTC).isoformat(), producer=PRODUCER, run=Run(runId=run_id), job=Job(namespace=NS, name=job), eventType=state,
                    inputs=[InputDataset(namespace=NS, name=i) for i in ins] if with_io else [], outputs=outputs)  # fmt: skip


def run_pipeline(
    client: OpenLineageClient,
    *,
    fail: str | None = None,
    hang: str | None = None,
    skip: tuple[str, ...] = (),
) -> dict[str, str]:
    """Runs the four jobs in order. `fail` job emits FAIL (and downstream jobs do not run); `hang` emits START only."""
    ids: dict[str, str] = {}
    broken = False
    for job in JOBS:
        if job in skip or broken:
            continue
        rid = ids[job] = str(uuid.uuid4())
        client.emit(_event(job, rid, RunState.START, with_io=False))
        if job == hang:
            broken = True
            continue
        if job == fail:
            client.emit(_event(job, rid, RunState.FAIL, with_io=False))
            broken = True
            continue
        client.emit(_event(job, rid, RunState.COMPLETE, with_io=True))
    return ids
