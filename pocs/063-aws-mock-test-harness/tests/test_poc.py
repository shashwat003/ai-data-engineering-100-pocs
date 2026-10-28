import os
import shutil

import boto3
import pytest
from aws_mock_harness import helpers
from aws_mock_harness.guard import RealAwsBlocked, forbid_real_aws, is_loopback
from aws_mock_harness.main import run
from aws_mock_harness.probe import run_probes
from aws_mock_harness.server import MockAws


# --- the harness used on itself ---------------------------------------------------------------------------
def test_isolation_a_creates_state(aws):
    helpers.make_bucket(aws, "leaky-bucket")
    assert "leaky-bucket" in [b["Name"] for b in aws.client("s3").list_buckets()["Buckets"]]


def test_isolation_b_sees_clean_state(aws):
    assert (
        aws.client("s3").list_buckets()["Buckets"] == []
    )  # runs after test a: reset removed its bucket


def test_fixtures_bucket_and_queue(aws, bucket, queue):
    helpers.put_objects(aws, bucket, {"a/1.txt": "x", "a/2.txt": b"y", "b/3.txt": "z"})
    assert helpers.list_keys(aws, bucket, "a/") == ["a/1.txt", "a/2.txt"]
    aws.client("sqs").send_message(QueueUrl=queue, MessageBody="hi")
    assert helpers.drain(aws, queue) == ["hi"]


def test_list_keys_paginates_past_1000(aws):
    helpers.make_bucket(aws, "many-objects")
    s3 = aws.client("s3")
    for i in range(1100):
        s3.put_object(Bucket="many-objects", Key=f"k{i:05d}", Body=b"")
    assert len(helpers.list_keys(aws, "many-objects")) == 1100


def test_dlq_redrive_configured(aws):
    url = helpers.make_queue(aws, "work", dlq=True, max_receive=2)
    attrs = aws.client("sqs").get_queue_attributes(QueueUrl=url, AttributeNames=["RedrivePolicy"])[
        "Attributes"
    ]
    assert '"maxReceiveCount": 2' in attrs["RedrivePolicy"]


def test_wait_for_success_and_timeout():
    helpers.wait_for(lambda: True)
    with pytest.raises(TimeoutError, match="never"):
        helpers.wait_for(lambda: False, timeout=0.2, what="never")


# --- safety ------------------------------------------------------------------------------------------------
def test_guard_blocks_clients_without_endpoint(aws):
    client = boto3.client(
        "s3", region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="y"
    )
    with pytest.raises(RealAwsBlocked):
        client.list_buckets()  # autouse guard from the plugin is active


def test_guard_allows_local_endpoint_and_restores(aws):
    assert aws.client("s3").list_buckets()["Buckets"] == []
    from botocore.httpsession import URLLib3Session

    before = URLLib3Session.send
    with forbid_real_aws():
        assert URLLib3Session.send is not before
    assert URLLib3Session.send is before


@pytest.mark.parametrize(
    "url,ok",
    [
        ("http://127.0.0.1:5000", True),
        ("http://localhost:1", True),
        ("http://[::1]:80", True),
        ("https://s3.eu-west-1.amazonaws.com", False),
        ("http://10.0.0.5", False),
        ("http://127.0.0.1.evil.com", False),
        ("", False),
    ],
)
def test_is_loopback(url, ok):
    assert is_loopback(url) is ok


def test_env_for_subprocesses_strips_real_credentials(aws, monkeypatch):
    for k in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_ROLE_ARN", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "REAL-LOOKING")
    env = aws.env()
    assert (
        env["AWS_ENDPOINT_URL"] == aws.endpoint
        and env["AWS_ACCESS_KEY_ID"] == "mock"
        and env["AWS_SECRET_ACCESS_KEY"] == "mock"
    )
    assert not {"AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_ROLE_ARN"} & set(env)
    assert os.environ["AWS_PROFILE"] == "REAL-LOOKING"  # the parent environment is untouched


# --- lifecycle ---------------------------------------------------------------------------------------------
def test_server_lifecycle_and_port_isolation():
    a, b = MockAws(), MockAws()
    assert a.port != b.port
    with a:
        assert a.client("s3").list_buckets()["Buckets"] == []
    assert a.proc is None
    a.stop()  # idempotent


def test_startup_failure_is_reported(monkeypatch):
    import aws_mock_harness.server as server

    monkeypatch.setattr(server, "_exe", lambda: "/bin/false")
    with pytest.raises(RuntimeError, match="exited"):
        MockAws(startup_timeout=3).start()


# --- plugin works for a downstream suite (pytester) -------------------------------------------------------
def test_plugin_in_a_fresh_project(pytester, monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parent.parent))
    pytester.makeconftest('pytest_plugins = ["aws_mock_harness.plugin"]')
    pytester.makepyfile(
        """
        import boto3, pytest
        from aws_mock_harness.guard import RealAwsBlocked

        def test_uses_fixture(aws, bucket):
            assert aws.client("s3").head_bucket(Bucket=bucket)

        def test_real_aws_is_blocked():
            with pytest.raises(RealAwsBlocked):
                boto3.client("sts", region_name="eu-west-1", aws_access_key_id="a", aws_secret_access_key="b").get_caller_identity()
        """
    )
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=2)


# --- measured parity ---------------------------------------------------------------------------------------
def test_probe_matrix_is_honest(aws):
    p = run_probes(aws)
    assert all(
        p[s]["supported"]
        for s in (
            "s3",
            "sqs",
            "dynamodb",
            "kinesis",
            "firehose",
            "events",
            "kms",
            "secretsmanager",
            "organizations",
        )
    )
    if (
        shutil.which("docker") is None
    ):  # moto executes Lambda code in Docker; without it the probe must say so
        assert p["lambda_invoke"]["supported"] is False, (
            "the harness must not pretend Lambda code runs without Docker"
        )


def test_demo_numbers():
    r = run(n_cycles=10)
    assert r["state_cleared_by_reset"] and r["real_aws_blocked"] and r["ms_per_test_cycle"] < 1000
