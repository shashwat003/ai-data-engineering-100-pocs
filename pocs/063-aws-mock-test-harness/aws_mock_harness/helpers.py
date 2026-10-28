"""Small helpers that keep AWS tests short and deterministic."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from .server import REGION


def make_bucket(aws, name: str, *, versioning: bool = False, encrypted: bool = True) -> str:
    s3 = aws.client("s3")
    s3.create_bucket(Bucket=name, CreateBucketConfiguration={"LocationConstraint": REGION})
    if versioning:
        s3.put_bucket_versioning(Bucket=name, VersioningConfiguration={"Status": "Enabled"})
    if encrypted:
        s3.put_bucket_encryption(
            Bucket=name,
            ServerSideEncryptionConfiguration={
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
            },
        )
    return name


def put_objects(aws, bucket: str, files: Mapping[str, bytes | str]) -> None:
    s3 = aws.client("s3")
    for key, body in files.items():
        s3.put_object(Bucket=bucket, Key=key, Body=body.encode() if isinstance(body, str) else body)


def list_keys(aws, bucket: str, prefix: str = "") -> list[str]:
    s3 = aws.client("s3")
    out, token = [], None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix, **({"ContinuationToken": token} if token else {})}
        r = s3.list_objects_v2(**kw)
        out += [o["Key"] for o in r.get("Contents", [])]
        token = r.get("NextContinuationToken")
        if not token:
            return sorted(out)


def make_queue(aws, name: str, *, dlq: bool = False, max_receive: int = 3) -> str:
    sqs = aws.client("sqs")
    url = sqs.create_queue(QueueName=name)["QueueUrl"]
    if dlq:
        durl = sqs.create_queue(QueueName=f"{name}-dlq")["QueueUrl"]
        darn = sqs.get_queue_attributes(QueueUrl=durl, AttributeNames=["QueueArn"])["Attributes"][
            "QueueArn"
        ]
        import json

        sqs.set_queue_attributes(
            QueueUrl=url,
            Attributes={
                "RedrivePolicy": json.dumps(
                    {"deadLetterTargetArn": darn, "maxReceiveCount": max_receive}
                )
            },
        )
    return url


def drain(aws, queue_url: str, limit: int = 100) -> list[str]:
    sqs = aws.client("sqs")
    out: list[str] = []
    while len(out) < limit:
        msgs = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10).get("Messages", [])
        if not msgs:
            return out
        for m in msgs:
            out.append(m["Body"])
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
    return out


def wait_for(
    condition: Callable[[], bool],
    *,
    timeout: float = 5.0,
    interval: float = 0.05,
    what: str = "condition",
) -> None:
    """Poll instead of sleeping a fixed time: fast when ready, a clear error when not."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(interval)
    raise TimeoutError(f"timed out after {timeout}s waiting for {what}")
