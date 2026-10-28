"""POC 058: producer retries, checkpointed consumer with crash/replay, Firehose path, compaction, on a local AWS mock."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from aws_mock_harness import helpers
from aws_mock_harness.server import MockAws
from botocore.exceptions import ClientError

from pocs_common import write_json

from .compact import compact, list_prefix, read_events
from .consumer import Consumer, Crash
from .producer import Producer

POC_DIR = Path(__file__).resolve().parent.parent
BUCKET, STREAM, TABLE, FH = "stream-lake", "events", "checkpoints", "events-fh"
T0 = datetime(2026, 3, 1, 8, 0, 0)


def make_events(n: int, late: int = 0) -> list[dict]:
    ev = [
        {
            "event_id": f"e{i:05d}",
            "ts": (T0 + timedelta(seconds=i * 20)).isoformat(),
            "user_id": i % 37,
            "value": i % 11,
        }
        for i in range(n)
    ]
    for j in range(late):  # events that arrive long after their event time
        ev.append(
            {
                "event_id": f"late{j:03d}",
                "ts": (T0 - timedelta(hours=5)).isoformat(),
                "user_id": j,
                "value": 1,
            }
        )
    return ev


class FlakyKinesis:
    """Wraps a client; every `every`-th record of put_records fails with a throughput error (the real API reports this per record)."""

    def __init__(self, inner, every: int = 7):
        self.inner, self.every, self.n = inner, every, 0

    def put_records(self, **kw):
        """Records whose turn comes up fail with a throughput error and are NOT stored; the rest are sent for real."""
        recs, out, ok_idx = kw["Records"], [], []
        for i, _ in enumerate(recs):
            self.n += 1
            if self.n % self.every == 0:
                out.append(
                    {
                        "ErrorCode": "ProvisionedThroughputExceededException",
                        "ErrorMessage": "slow down",
                    }
                )
            else:
                out.append(None)
                ok_idx.append(i)
        if ok_idx:
            resp = self.inner.put_records(
                StreamName=kw["StreamName"], Records=[recs[i] for i in ok_idx]
            )
            for i, r in zip(ok_idx, resp["Records"], strict=True):
                out[i] = r
        return {"FailedRecordCount": sum(1 for o in out if "ErrorCode" in o), "Records": out}

    def __getattr__(self, name):
        return getattr(self.inner, name)


def run() -> dict:
    res: dict = {}
    with MockAws() as aws:
        k, ddb, s3, fh = (
            aws.client("kinesis"),
            aws.client("dynamodb"),
            aws.client("s3"),
            aws.client("firehose"),
        )
        helpers.make_bucket(aws, BUCKET)
        k.create_stream(StreamName=STREAM, ShardCount=2)
        ddb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        role = aws.client("iam").create_role(RoleName="fh", AssumeRolePolicyDocument="{}")["Role"][
            "Arn"
        ]
        fh.create_delivery_stream(
            DeliveryStreamName=FH,
            DeliveryStreamType="DirectPut",
            ExtendedS3DestinationConfiguration={
                "RoleARN": role,
                "BucketARN": f"arn:aws:s3:::{BUCKET}",
                "Prefix": "firehose/",
            },
        )
        events = make_events(600, late=5)
        # 1) producer with injected partial failures: no loss, no duplicates from retries
        stats = Producer(FlakyKinesis(k), STREAM, sleep=lambda s: None).send(events)
        res["producer"] = {
            "sent_records": stats.sent,
            "retried_records": stats.retries,
            "gave_up": len(stats.gave_up),
        }
        # 2) consumer crashes after the sink write but before the checkpoint -> replay -> idempotent sink
        c = Consumer(k, ddb, s3, stream=STREAM, table=TABLE, bucket=BUCKET, batch_limit=100)
        try:
            c.poll(crash_before_commit=lambda b: b == 2)
        except Crash:
            res["crashed_once"] = True
        s = c.poll()
        res["consumer_after_replay"] = s
        keys = list_prefix(s3, BUCKET, "events/") + list_prefix(s3, BUCKET, "late/")
        got = read_events(s3, BUCKET, keys)
        ids = [e["event_id"] for e in got]
        res["sink"] = {
            "objects": len(keys),
            "events": len(ids),
            "unique_event_ids": len(set(ids)),
            "expected": len(events),
            "late_prefix_events": len(read_events(s3, BUCKET, list_prefix(s3, BUCKET, "late/"))),
        }
        res["effectively_once"] = len(ids) == len(set(ids)) == len(events)
        # 3) the Firehose path (managed batching) delivers at-least-once to S3; send overlapping batches to create duplicates
        for chunk in (events[:300], events[200:500]):
            for i in range(0, len(chunk), 100):
                fh.put_record_batch(
                    DeliveryStreamName=FH,
                    Records=[{"Data": (json.dumps(e) + "\n").encode()} for e in chunk[i : i + 100]],
                )
        fh_keys = list_prefix(s3, BUCKET, "firehose/")
        res["firehose_objects_before_compaction"] = len(fh_keys)
        res["firehose_compaction"] = compact(s3, BUCKET, "firehose/", "fh-compacted")
        res["firehose_unique_after"] = len(
            {
                e["event_id"]
                for e in read_events(s3, BUCKET, list_prefix(s3, BUCKET, "fh-compacted/"))
            }
        )
        # 4) poison record in the stream is skipped and counted, not blocking
        k.put_record(StreamName=STREAM, Data=b"{not json", PartitionKey="x")
        res["poison_skipped"] = c.poll()["poison_skipped"]
        # 5) oversize record rejected by the producer, never sent
        big = {"event_id": "big1", "ts": T0.isoformat(), "user_id": 1, "value": "x" * 1_100_000}
        res["oversize_rejected"] = Producer(k, STREAM).send([big]).rejected_oversize
        try:
            k.put_record(StreamName="no-such-stream", Data=b"x", PartitionKey="k")
        except ClientError as e:
            res["missing_stream_error"] = e.response["Error"]["Code"]
    return res


def main() -> None:
    r = run()
    write_json(POC_DIR / "results" / "metrics.json", r)
    md = [
        "# Results (measured by `python -m kinesis_firehose_ingest.main`; Kinesis, Firehose, S3, DynamoDB on a LOCAL moto account; failures injected by wrappers)",
        "",
    ] + [f"- **{k}**: {v}" for k, v in r.items()]
    (POC_DIR / "results" / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
