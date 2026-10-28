import json

import pytest
from aws_mock_harness import helpers
from botocore.exceptions import ClientError
from eventbridge_lambda_etl.handler import PoisonMessage, handler, parse_event, validate_row
from eventbridge_lambda_etl.main import HEADER, csv_body, run
from eventbridge_lambda_etl.pipeline import (
    CURATED,
    RAW,
    make_env,
    poll_once,
    provision,
    publish_s3_event,
    run_until_idle,
    upload,
)


@pytest.fixture()
def stack(aws):
    q = provision(aws, max_receive=2)
    return aws, q, make_env(aws)


def ev(key="incoming/a.csv", bucket=RAW, etag="e", source="aws.s3", dtype="Object Created"):
    return json.dumps(
        {
            "source": source,
            "detail-type": dtype,
            "detail": {"bucket": {"name": bucket}, "object": {"key": key, "etag": etag}},
        }
    )


@pytest.mark.parametrize(
    "body",
    [
        "{",
        "[]",
        json.dumps({"detail": {}}),
        ev(key="incoming/../x.csv"),
        ev(key="incoming/a.txt"),
        ev(key="other/a.csv"),
        ev(source="aws.ec2"),
        ev(dtype="Object Deleted"),
        ev(key="incoming/" + "a" * 300 + ".csv"),
    ],
)
def test_parse_event_rejects_poison(body):
    with pytest.raises(PoisonMessage):
        parse_event(body)


def test_parse_event_accepts_valid():
    assert parse_event(ev()) == (RAW, "incoming/a.csv", "e")


@pytest.mark.parametrize(
    "row,why",
    [
        (
            {
                "order_id": "1",
                "customer_id": "2",
                "amount": "3.5",
                "currency": "EUR",
                "order_date": "2026-01-01",
            },
            None,
        ),
        (
            {
                "order_id": "-1",
                "customer_id": "2",
                "amount": "3.5",
                "currency": "EUR",
                "order_date": "2026-01-01",
            },
            "negative id",
        ),
        (
            {
                "order_id": "1",
                "customer_id": "2",
                "amount": "-3",
                "currency": "EUR",
                "order_date": "2026-01-01",
            },
            "negative amount",
        ),
        (
            {
                "order_id": "1",
                "customer_id": "2",
                "amount": "3",
                "currency": "XXX",
                "order_date": "2026-01-01",
            },
            "unknown currency",
        ),
        (
            {
                "order_id": "1",
                "customer_id": "2",
                "amount": "3",
                "currency": "EUR",
                "order_date": "2026-13-01",
            },
            "unparseable value",
        ),
        (
            {
                "order_id": "a",
                "customer_id": "2",
                "amount": "3",
                "currency": "EUR",
                "order_date": "2026-01-01",
            },
            "unparseable value",
        ),
        ({"order_id": "1"}, "unparseable value"),
    ],
)
def test_validate_row(row, why):
    assert validate_row(row) == why


def test_valid_and_invalid_rows_are_split_and_reconciled(stack):
    aws, q, env = stack
    upload(aws, "incoming/f.csv", csv_body(5, bad=["9,1,-1,EUR,2026-03-01"]))
    run_until_idle(aws, env, q["queue"])
    assert helpers.list_keys(aws, CURATED) == ["curated/f.parquet", "quarantine/f.csv"]
    q_rows = (
        aws.client("s3")
        .get_object(Bucket=CURATED, Key="quarantine/f.csv")["Body"]
        .read()
        .decode()
        .splitlines()
    )
    assert (
        len(q_rows) == 2 and "negative amount" in q_rows[1] and q_rows[1].startswith("7,")
    )  # line number 7 of the file


def test_wrong_prefix_is_never_routed(stack):
    aws, q, env = stack
    upload(aws, "scratch/x.csv", csv_body(2))
    assert poll_once(aws, env, q["queue"])["received"] == 0


def test_duplicate_events_processed_once(stack):
    aws, q, env = stack
    upload(aws, "incoming/f.csv", csv_body(3))
    etag = aws.client("s3").head_object(Bucket=RAW, Key="incoming/f.csv")["ETag"].strip('"')
    for _ in range(4):
        publish_s3_event(aws, RAW, "incoming/f.csv", etag)
    log = run_until_idle(aws, env, q["queue"])
    statuses = sorted(x["status"] for r in log for x in r["results"])
    assert statuses == ["duplicate"] * 4 + ["ok"]
    completed = aws.client("dynamodb").scan(TableName="etl-idempotency")["Count"]
    assert completed == 1


def test_changed_object_same_key_is_reprocessed(stack):
    aws, q, env = stack
    upload(aws, "incoming/f.csv", csv_body(2))
    run_until_idle(aws, env, q["queue"])
    upload(aws, "incoming/f.csv", csv_body(4))  # new etag
    log = run_until_idle(aws, env, q["queue"])
    assert [x["status"] for r in log for x in r["results"]] == ["ok"] and log[0]["results"][0][
        "rows_ok"
    ] == 4


def test_transient_error_releases_claim_and_retries(stack):
    aws, q, env = stack
    upload(aws, "incoming/f.csv", csv_body(2))
    real, n = env.s3.get_object, {"n": 0}

    def flaky(**kw):
        n["n"] += 1
        if n["n"] == 1:
            raise ClientError({"Error": {"Code": "InternalError", "Message": "x"}}, "GetObject")
        return real(**kw)

    env.s3.get_object = flaky
    log = run_until_idle(aws, env, q["queue"])
    assert [x["status"] for r in log for x in r["results"]] == ["retry", "ok"]
    assert "curated/f.parquet" in helpers.list_keys(aws, CURATED)


def test_poison_goes_to_dlq_after_max_receive_and_good_messages_unaffected(stack):
    aws, q, env = stack
    aws.client("sqs").send_message(QueueUrl=q["queue"], MessageBody="garbage")
    upload(aws, "incoming/ok.csv", csv_body(2))
    log = run_until_idle(aws, env, q["queue"])
    all_results = [x for r in log for x in r["results"]]
    assert sum(x["status"] == "poison" for x in all_results) == 2 and any(
        x["status"] == "ok" for x in all_results
    )
    assert helpers.drain(aws, q["dlq"]) == ["garbage"]


def test_missing_columns_and_empty_file_are_poison(stack):
    aws, q, env = stack
    upload(aws, "incoming/nohdr.csv", "a,b\n1,2\n")
    upload(aws, "incoming/empty.csv", "")
    log = run_until_idle(aws, env, q["queue"])
    assert {x["status"] for r in log for x in r["results"]} == {"poison"}


def test_handler_reports_only_failed_items(stack):
    aws, q, env = stack
    out = handler({"Records": [{"messageId": "m1", "body": "bad"}]}, env)
    assert out["batchItemFailures"] == [{"itemIdentifier": "m1"}]
    assert handler({"Records": []}, env) == {"batchItemFailures": [], "results": []}


def test_oversized_object_rejected(stack, monkeypatch):
    aws, q, env = stack
    import eventbridge_lambda_etl.handler as h

    monkeypatch.setattr(h, "MAX_BYTES", 10)
    upload(aws, "incoming/big.csv", csv_body(5))
    log = run_until_idle(aws, env, q["queue"])
    assert {x["status"] for r in log for x in r["results"]} == {"poison"}


def test_end_to_end_demo_numbers():
    r = run()
    assert (
        r["reconciliation"]["balanced"] and r["day2_rows_curated"] == 10 and r["dlq_messages"] == 2
    )
    assert (
        r["duplicate_results"] == ["duplicate"]
        and r["transient_statuses"] == ["retry", "ok"]
        and r["main_queue_empty_at_end"]
    )
    assert HEADER.startswith("order_id")


def test_non_utf8_file_is_poison_not_a_crash(stack):
    aws, q, env = stack
    aws.client("s3").put_object(Bucket=RAW, Key="incoming/bin.csv", Body=b"\xff\xfe\x00bad")
    publish_s3_event(aws, RAW, "incoming/bin.csv", "e1")
    log = run_until_idle(aws, env, q["queue"])
    assert {x["status"] for r in log for x in r["results"]} == {"poison"}


def test_unexpected_exception_fails_only_that_item(stack):
    aws, q, env = stack
    env.ddb = None  # simulates a bug: AttributeError inside the handler
    out = handler(
        {"Records": [{"messageId": "m1", "body": ev()}, {"messageId": "m2", "body": "bad"}]}, env
    )
    assert [f["itemIdentifier"] for f in out["batchItemFailures"]] == ["m1", "m2"]
    assert [r["status"] for r in out["results"]] == ["error", "poison"]
