import json

import pytest
from aws_mock_harness import helpers
from botocore.exceptions import ClientError
from kinesis_firehose_ingest.compact import compact, list_prefix, read_events
from kinesis_firehose_ingest.consumer import Consumer, Crash, hour_prefix
from kinesis_firehose_ingest.main import FlakyKinesis, make_events, run
from kinesis_firehose_ingest.producer import MAX_RECORDS, Producer


@pytest.fixture()
def stack(aws):
    k, ddb, s3 = aws.client("kinesis"), aws.client("dynamodb"), aws.client("s3")
    helpers.make_bucket(aws, "lake-bucket")
    k.create_stream(StreamName="st", ShardCount=2)
    ddb.create_table(
        TableName="cp",
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    return k, ddb, s3


def consumer(stack, **kw):
    k, ddb, s3 = stack
    return Consumer(k, ddb, s3, stream="st", table="cp", bucket="lake-bucket", **kw)


class Recorder:
    def __init__(self, inner):
        self.inner, self.sizes = inner, []

    def put_records(self, **kw):
        self.sizes.append(len(kw["Records"]))
        return self.inner.put_records(**kw)


def test_producer_batches_at_most_500(stack):
    k, _, _ = stack
    rec = Recorder(k)
    st = Producer(rec, "st").send(make_events(1200))
    assert st.sent == 1200 and max(rec.sizes) <= MAX_RECORDS and rec.sizes == [500, 500, 200]


def test_producer_retries_only_failed_records_without_duplicates(stack):
    k, _, _ = stack
    st = Producer(FlakyKinesis(k, every=5), "st", sleep=lambda s: None).send(make_events(100))
    assert st.sent == 100 and st.retries > 0 and st.gave_up == []
    c = consumer(stack)
    c.poll()
    ids = [
        e["event_id"]
        for e in read_events(
            stack[2],
            "lake-bucket",
            list_prefix(stack[2], "lake-bucket", "events/")
            + list_prefix(stack[2], "lake-bucket", "late/"),
        )
    ]
    assert len(ids) == len(set(ids)) == 100


def test_producer_gives_up_after_max_attempts_and_reports(stack):
    class Always:
        def put_records(self, **kw):
            return {
                "FailedRecordCount": len(kw["Records"]),
                "Records": [{"ErrorCode": "X"} for _ in kw["Records"]],
            }

    sleeps = []
    st = Producer(Always(), "st", max_attempts=3, sleep=sleeps.append).send(make_events(4))
    assert st.sent == 0 and len(st.gave_up) == 4 and len(sleeps) == 3 and sleeps == sorted(sleeps)


def test_producer_rejects_oversize_and_handles_empty(stack):
    k, _, _ = stack
    big = {"event_id": "big", "ts": "2026-03-01T08:00:00", "user_id": 1, "value": "x" * 1_100_000}
    st = Producer(k, "st").send([big, *make_events(2)])
    assert st.rejected_oversize == ["big"] and st.sent == 2
    assert Producer(k, "st").send([]).sent == 0


def test_consumer_resumes_from_checkpoint_and_does_not_reprocess(stack):
    k, _, _ = stack
    Producer(k, "st").send(make_events(50))
    c = consumer(stack)
    assert c.poll()["records"] == 50
    assert c.poll()["records"] == 0
    Producer(k, "st").send(
        [{"event_id": "new1", "ts": "2026-03-01T11:00:00", "user_id": 1, "value": 1}]
    )
    assert c.poll()["records"] == 1


def test_crash_before_commit_replays_without_duplicates(stack):
    k, _, s3 = stack
    Producer(k, "st").send(make_events(300))
    c = consumer(stack, batch_limit=50)
    with pytest.raises(Crash):
        c.poll(crash_before_commit=lambda b: b == 2)
    c.poll()
    keys = list_prefix(s3, "lake-bucket", "events/") + list_prefix(s3, "lake-bucket", "late/")
    ids = [e["event_id"] for e in read_events(s3, "lake-bucket", keys)]
    assert len(ids) == len(set(ids)) == 300  # replayed batch overwrote its own objects


def test_late_events_are_routed_by_watermark_and_replay_is_deterministic(stack):
    k, _, s3 = stack
    ev = make_events(30, late=3)
    Producer(k, "st").send(ev)
    c = consumer(stack)
    c.poll()
    late = read_events(s3, "lake-bucket", list_prefix(s3, "lake-bucket", "late/"))
    assert {e["event_id"] for e in late} >= {"late000", "late001", "late002"}
    assert all(e["ts"] < "2026-03-01T08:00:00" or e["event_id"].startswith("e") for e in late)


def test_poison_record_is_skipped_and_counted(stack):
    k, _, _ = stack
    k.put_record(StreamName="st", Data=b"{nope", PartitionKey="a")
    k.put_record(
        StreamName="st", Data=json.dumps({"event_id": "x", "user_id": 1}).encode(), PartitionKey="a"
    )  # no ts
    Producer(k, "st").send(make_events(3))
    s = consumer(stack).poll()
    assert s["poison_skipped"] == 2 and s["records"] == 5


def test_expired_iterator_ends_poll_without_crashing(stack):
    k, ddb, s3 = stack

    class Expiring:
        def __init__(self, inner):
            self.inner = inner

        def get_records(self, **kw):
            raise ClientError(
                {"Error": {"Code": "ExpiredIteratorException", "Message": "x"}}, "GetRecords"
            )

        def __getattr__(self, n):
            return getattr(self.inner, n)

    Producer(k, "st").send(make_events(5))
    c = Consumer(Expiring(k), ddb, s3, stream="st", table="cp", bucket="lake-bucket")
    assert c.poll()["records"] == 0


def test_hour_prefix():
    assert hour_prefix("2026-03-01T08:59:59") == "dt=2026-03-01/hh=08"


def test_compaction_dedupes_partitions_by_event_time_and_deletes_sources(stack):
    _, _, s3 = stack
    ev = make_events(120)
    for chunk in (ev[:80], ev[40:]):
        s3.put_object(
            Bucket="lake-bucket",
            Key=f"fh/{len(chunk)}-{chunk[0]['event_id']}.jsonl",
            Body="".join(json.dumps(e) + "\n" for e in chunk).encode(),
        )
    r = compact(s3, "lake-bucket", "fh/", "out")
    assert (
        r["events_in"] == 160
        and r["events_out"] == 120
        and r["duplicates_dropped"] == 40
        and r["objects_before"] == 2
    )
    assert list_prefix(s3, "lake-bucket", "fh/") == [] and all(
        "dt=2026-03-01/hh=" in k for k in list_prefix(s3, "lake-bucket", "out/")
    )
    assert len(read_events(s3, "lake-bucket", list_prefix(s3, "lake-bucket", "out/"))) == 120


def test_compaction_keeps_sources_if_a_write_fails(stack):
    _, _, s3 = stack
    s3.put_object(
        Bucket="lake-bucket", Key="fh/a.jsonl", Body=(json.dumps(make_events(1)[0]) + "\n").encode()
    )

    class Failing:
        def __getattr__(self, n):
            return getattr(s3, n)

        def put_object(self, **kw):
            raise ClientError({"Error": {"Code": "InternalError", "Message": "x"}}, "PutObject")

    with pytest.raises(ClientError):
        compact(Failing(), "lake-bucket", "fh/", "out")
    assert list_prefix(s3, "lake-bucket", "fh/") == ["fh/a.jsonl"]


def test_demo_end_to_end():
    r = run()
    assert (
        r["effectively_once"]
        and r["producer"]["gave_up"] == 0
        and r["producer"]["retried_records"] > 0
    )
    assert (
        r["firehose_compaction"]["duplicates_dropped"] == 100 and r["firehose_unique_after"] == 500
    )
    assert r["poison_skipped"] == 1 and r["oversize_rejected"] == ["big1"]
