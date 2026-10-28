"""Lambda handler for an SQS-triggered ETL step.

Event shape: SQS batch -> each record body is an EventBridge event ("Object Created" from S3).
Behaviour: validate the event, skip duplicates (DynamoDB conditional write), read the CSV, split valid rows
(written as Parquet to `curated/`) from rejects (CSV in `quarantine/`), emit an `etl.completed` event.
Returns the SQS "partial batch response": only records that failed transiently are retried.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq
from botocore.exceptions import ClientError

CURRENCIES = {"EUR", "USD", "GBP"}
KEY_RE = re.compile(r"^incoming/[A-Za-z0-9_\-./]{1,200}\.csv$")
MAX_BYTES = 5_000_000
COLUMNS = ["order_id", "customer_id", "amount", "currency", "order_date"]


class PoisonMessage(ValueError):
    """The message can never succeed (bad shape); retrying is pointless, so it goes to the DLQ via maxReceiveCount."""


class Env:
    """Clients and names injected by the caller (the Lambda runtime would build these from environment variables)."""

    def __init__(self, s3, dynamodb, events, table: str, curated_bucket: str):
        self.s3, self.ddb, self.events, self.table, self.curated = (
            s3,
            dynamodb,
            events,
            table,
            curated_bucket,
        )


def parse_event(body: str) -> tuple[str, str, str]:
    try:
        ev = json.loads(body)
        d = ev["detail"]
        bucket, key = d["bucket"]["name"], d["object"]["key"]
        etag = d["object"].get("etag", "")
    except (ValueError, KeyError, TypeError) as e:
        raise PoisonMessage(f"unparseable event: {type(e).__name__}") from e
    if ev.get("source") != "aws.s3" or ev.get("detail-type") != "Object Created":
        raise PoisonMessage("not an S3 Object Created event")
    if not isinstance(key, str) or not KEY_RE.match(key) or ".." in key:
        raise PoisonMessage("object key outside the allowed incoming/*.csv shape")
    return bucket, key, etag


def validate_row(r: dict) -> str | None:
    try:
        if int(r["order_id"]) < 0 or int(r["customer_id"]) < 0:
            return "negative id"
        if float(r["amount"]) < 0:
            return "negative amount"
        if r["currency"] not in CURRENCIES:
            return "unknown currency"
        date.fromisoformat(r["order_date"])
    except (KeyError, ValueError, TypeError):
        return "unparseable value"
    return None


def process_object(env: Env, bucket: str, key: str, etag: str) -> dict:
    idem = f"{bucket}/{key}#{etag}"
    try:
        env.ddb.put_item(
            TableName=env.table,
            Item={"pk": {"S": idem}},
            ConditionExpression="attribute_not_exists(pk)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return {"status": "duplicate", "key": key}
        raise
    try:
        return _transform(env, bucket, key)
    except Exception:
        # any failure (transient or poison) releases the claim; otherwise the retry would be skipped as a "duplicate"
        env.ddb.delete_item(TableName=env.table, Key={"pk": {"S": idem}})
        raise


def _transform(env: Env, bucket: str, key: str) -> dict:
    head = env.s3.head_object(Bucket=bucket, Key=key)
    if head["ContentLength"] > MAX_BYTES:
        raise PoisonMessage("object too large")
    raw = env.s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    try:
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    except (UnicodeDecodeError, csv.Error) as e:
        raise PoisonMessage(f"file is not valid UTF-8 CSV: {type(e).__name__}") from e
    if not rows or set(COLUMNS) - set(rows[0]):
        raise PoisonMessage("missing header columns")
    good, bad = [], []
    for i, r in enumerate(rows, start=2):
        why = validate_row(r)
        (bad if why else good).append((i, r, why))
    base = key.removeprefix("incoming/").removesuffix(".csv")
    if good:
        table = _typed(good)
        buf = io.BytesIO()
        pq.write_table(table, buf)
        env.s3.put_object(Bucket=env.curated, Key=f"curated/{base}.parquet", Body=buf.getvalue())
    if bad:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["line", "reason", *COLUMNS])
        w.writerows([i, why, *[r.get(c, "") for c in COLUMNS]] for i, r, why in bad)
        env.s3.put_object(
            Bucket=env.curated, Key=f"quarantine/{base}.csv", Body=out.getvalue().encode()
        )
    env.events.put_events(
        Entries=[
            {
                "Source": "etl",
                "DetailType": "etl.completed",
                "Detail": json.dumps(
                    {
                        "key": key,
                        "rows_in": len(rows),
                        "rows_ok": len(good),
                        "rows_rejected": len(bad),
                    }
                ),
            }
        ]
    )
    return {
        "status": "ok",
        "key": key,
        "rows_in": len(rows),
        "rows_ok": len(good),
        "rows_rejected": len(bad),
    }


def _typed(good):
    cols = list(zip(*[[g[1][c] for c in COLUMNS] for g in good], strict=True))
    return pa.table(
        {
            "order_id": pa.array([int(x) for x in cols[0]], pa.int64()),
            "customer_id": pa.array([int(x) for x in cols[1]], pa.int64()),
            "amount": pa.array([float(x) for x in cols[2]], pa.float64()),
            "currency": pa.array(list(cols[3]), pa.string()),
            "order_date": pa.array([date.fromisoformat(x) for x in cols[4]], pa.date32()),
        }
    )


def handler(event: dict, env: Env) -> dict:
    failures = []
    results = []
    for rec in event.get("Records", []):
        try:
            bucket, key, etag = parse_event(rec["body"])
            results.append(process_object(env, bucket, key, etag))
        except PoisonMessage as e:
            results.append({"status": "poison", "reason": str(e)})
            failures.append(
                {"itemIdentifier": rec["messageId"]}
            )  # retried until maxReceiveCount, then the DLQ takes it
        except ClientError as e:  # transient AWS error: retry
            results.append({"status": "retry", "reason": e.response["Error"]["Code"]})
            failures.append({"itemIdentifier": rec["messageId"]})
        except Exception as e:  # unexpected bug: fail this item only, never the whole batch
            results.append({"status": "error", "reason": type(e).__name__})
            failures.append({"itemIdentifier": rec["messageId"]})
    return {"batchItemFailures": failures, "results": results}
