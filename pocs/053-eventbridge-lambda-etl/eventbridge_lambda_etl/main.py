"""POC 053: event-driven ETL with idempotency, quarantine, retries and a dead-letter queue on a local AWS mock."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pyarrow.parquet as pq
from aws_mock_harness import helpers
from aws_mock_harness.server import MockAws

from pocs_common import write_json

from .pipeline import (
    CURATED,
    RAW,
    make_env,
    poll_once,
    provision,
    publish_s3_event,
    run_until_idle,
    upload,
)

POC_DIR = Path(__file__).resolve().parent.parent
HEADER = "order_id,customer_id,amount,currency,order_date\n"


def csv_body(n_good: int, bad: list[str] = (), start: int = 1) -> str:
    rows = [f"{start + i},{100 + i},{10.5 + i:.2f},EUR,2026-03-01" for i in range(n_good)]
    return HEADER + "\n".join([*rows, *bad]) + "\n"


def run() -> dict:
    res: dict = {}
    with MockAws() as aws:
        q = provision(aws, max_receive=3)
        env = make_env(aws)
        # 1) good file + file with rejects + file outside the routed prefix
        upload(aws, "incoming/day1.csv", csv_body(20))
        upload(
            aws,
            "incoming/day2.csv",
            csv_body(
                10,
                bad=[
                    "x,1,5,EUR,2026-03-01",
                    "99,1,-4,EUR,2026-03-01",
                    "98,1,4,JPY,2026-03-01",
                    "97,1,4,EUR,not-a-date",
                ],
                start=100,
            ),
        )
        upload(aws, "scratch/ignored.csv", csv_body(5))
        log = run_until_idle(aws, env, q["queue"])
        res["messages_processed"] = sum(r["received"] for r in log)
        res["curated_keys"] = helpers.list_keys(aws, CURATED, "curated/")
        res["quarantine_keys"] = helpers.list_keys(aws, CURATED, "quarantine/")
        t = pq.read_table(
            io.BytesIO(
                aws.client("s3")
                .get_object(Bucket=CURATED, Key="curated/day2.parquet")["Body"]
                .read()
            )
        )
        res["day2_rows_curated"] = t.num_rows
        res["day2_schema"] = {f.name: str(f.type) for f in t.schema}
        q2 = (
            aws.client("s3")
            .get_object(Bucket=CURATED, Key="quarantine/day2.csv")["Body"]
            .read()
            .decode()
            .splitlines()
        )
        res["day2_quarantine_reasons"] = sorted({ln.split(",")[1] for ln in q2[1:]})
        # 2) duplicate delivery of the same event is processed once
        etag = aws.client("s3").head_object(Bucket=RAW, Key="incoming/day1.csv")["ETag"].strip('"')
        for _ in range(3):
            publish_s3_event(aws, RAW, "incoming/day1.csv", etag)
        dup = run_until_idle(aws, env, q["queue"])
        res["duplicate_results"] = sorted({x["status"] for r in dup for x in r["results"]})
        # 3) transient failure: S3 read fails once, the batch item is retried and then succeeds
        upload(aws, "incoming/flaky.csv", csv_body(3, start=500))
        from botocore.exceptions import ClientError

        real_get, calls = env.s3.get_object, {"n": 0}

        def flaky_get(**kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ClientError(
                    {"Error": {"Code": "InternalError", "Message": "boom"}}, "GetObject"
                )
            return real_get(**kw)

        env.s3.get_object = flaky_get
        flog = run_until_idle(aws, env, q["queue"])
        env.s3.get_object = real_get
        res["transient_statuses"] = [x["status"] for r in flog for x in r["results"]]
        # 4) poison messages: bad event shapes are retried up to maxReceiveCount, then land in the DLQ
        sqs = aws.client("sqs")
        for body in (
            "not json",
            json.dumps(
                {
                    "source": "aws.s3",
                    "detail-type": "Object Created",
                    "detail": {
                        "bucket": {"name": RAW},
                        "object": {"key": "incoming/../../etc/passwd.csv"},
                    },
                }
            ),
        ):
            sqs.send_message(QueueUrl=q["queue"], MessageBody=body)
        plog = run_until_idle(aws, env, q["queue"], max_polls=40)
        res["poison_attempts"] = sum(
            1 for r in plog for x in r["results"] if x["status"] == "poison"
        )
        res["dlq_messages"] = len(helpers.drain(aws, q["dlq"]))
        res["main_queue_empty_at_end"] = poll_once(aws, env, q["queue"])["received"] == 0
        # 5) reconciliation: every input row is either curated or quarantined
        totals = {"in": 0, "ok": 0, "rejected": 0}
        for key in ("day1", "day2", "flaky"):
            body = (
                aws.client("s3")
                .get_object(Bucket=RAW, Key=f"incoming/{key}.csv")["Body"]
                .read()
                .decode()
                .splitlines()[1:]
            )
            totals["in"] += len([b for b in body if b])
        res["curated_keys_final"] = helpers.list_keys(aws, CURATED, "curated/")
        for k in res["curated_keys_final"]:
            totals["ok"] += pq.read_table(
                io.BytesIO(aws.client("s3").get_object(Bucket=CURATED, Key=k)["Body"].read())
            ).num_rows
        for k in helpers.list_keys(aws, CURATED, "quarantine/"):
            totals["rejected"] += (
                len(
                    aws.client("s3")
                    .get_object(Bucket=CURATED, Key=k)["Body"]
                    .read()
                    .decode()
                    .splitlines()
                )
                - 1
            )
        res["reconciliation"] = {
            **totals,
            "balanced": totals["in"] == totals["ok"] + totals["rejected"],
        }
    return res


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    md = [
        "# Results (measured by `python -m eventbridge_lambda_etl.main`; local moto account; Lambda invocation and S3->EventBridge emission SIMULATED)",
        "",
    ] + [f"- **{k}**: {v}" for k, v in r.items()]
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
