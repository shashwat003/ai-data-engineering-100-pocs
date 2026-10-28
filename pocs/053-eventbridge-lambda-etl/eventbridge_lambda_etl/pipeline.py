"""Provision the event plumbing in the mock account and emulate the Lambda SQS event-source mapping.

SIMULATED parts (no Lambda service is used): S3 does not emit EventBridge events to the mock, so `upload` publishes the
event S3 would publish; and `poll_once` plays the role of the event source mapping (batching, partial-batch deletes).
"""

from __future__ import annotations

import json
import time

from aws_mock_harness import helpers

from .handler import Env, handler

RAW, CURATED, QUEUE, TABLE = "raw-landing", "curated-zone", "etl-work", "etl-idempotency"
PATTERN = {
    "source": ["aws.s3"],
    "detail-type": ["Object Created"],
    "detail": {"bucket": {"name": [RAW]}, "object": {"key": [{"prefix": "incoming/"}]}},
}


def provision(aws, max_receive: int = 3) -> dict:
    helpers.make_bucket(aws, RAW)
    helpers.make_bucket(aws, CURATED)
    queue = helpers.make_queue(aws, QUEUE, dlq=True, max_receive=max_receive)
    sqs = aws.client("sqs")
    sqs.set_queue_attributes(
        QueueUrl=queue, Attributes={"VisibilityTimeout": "0"}
    )  # make retries immediate in tests
    qarn = sqs.get_queue_attributes(QueueUrl=queue, AttributeNames=["QueueArn"])["Attributes"][
        "QueueArn"
    ]
    aws.client("dynamodb").create_table(
        TableName=TABLE,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ev = aws.client("events")
    ev.put_rule(Name="raw-csv-created", EventPattern=json.dumps(PATTERN))
    ev.put_targets(Rule="raw-csv-created", Targets=[{"Id": "work-queue", "Arn": qarn}])
    return {"queue": queue, "dlq": queue.rsplit("/", 1)[0] + f"/{QUEUE}-dlq"}


def make_env(aws) -> Env:
    return Env(aws.client("s3"), aws.client("dynamodb"), aws.client("events"), TABLE, CURATED)


def upload(aws, key: str, body: str, *, publish: bool = True, bucket: str = RAW) -> None:
    r = aws.client("s3").put_object(Bucket=bucket, Key=key, Body=body.encode())
    if publish:
        publish_s3_event(aws, bucket, key, r["ETag"].strip('"'))


def publish_s3_event(aws, bucket: str, key: str, etag: str) -> None:
    detail = {"bucket": {"name": bucket}, "object": {"key": key, "etag": etag}}
    aws.client("events").put_events(
        Entries=[{"Source": "aws.s3", "DetailType": "Object Created", "Detail": json.dumps(detail)}]
    )


def poll_once(aws, env: Env, queue: str, batch_size: int = 10, fn=handler) -> dict:
    """One poll of the 'event source mapping': receive up to batch_size messages, invoke, delete the ones reported successful."""
    sqs = aws.client("sqs")
    msgs = sqs.receive_message(
        QueueUrl=queue, MaxNumberOfMessages=batch_size, AttributeNames=["ApproximateReceiveCount"]
    ).get("Messages", [])
    if not msgs:
        return {"received": 0, "failed": 0, "results": []}
    out = fn({"Records": [{"messageId": m["MessageId"], "body": m["Body"]} for m in msgs]}, env)
    failed = {f["itemIdentifier"] for f in out["batchItemFailures"]}
    for m in msgs:
        if m["MessageId"] not in failed:
            sqs.delete_message(QueueUrl=queue, ReceiptHandle=m["ReceiptHandle"])
    return {"received": len(msgs), "failed": len(failed), "results": out["results"]}


def run_until_idle(aws, env: Env, queue: str, max_polls: int = 30, fn=handler) -> list[dict]:
    log = []
    for _ in range(max_polls):
        r = poll_once(aws, env, queue, fn=fn)
        if not r["received"]:
            break
        log.append(r)
        time.sleep(0.01)
    return log
