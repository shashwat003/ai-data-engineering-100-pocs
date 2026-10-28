"""Measure what the mock actually supports: one tiny create/use round-trip per service. Honest parity table, not a claim."""

from __future__ import annotations

import json
from collections.abc import Callable

from .server import MockAws


def _ok(fn: Callable[[], object]) -> tuple[bool, str]:
    try:
        fn()
        return True, ""
    except Exception as e:  # any failure counts as 'not supported by the mock'
        return False, f"{type(e).__name__}: {str(e)[:80]}"


def probes(aws: MockAws) -> dict[str, Callable[[], object]]:
    def s3():
        c = aws.client("s3")
        c.create_bucket(
            Bucket="probe-bucket", CreateBucketConfiguration={"LocationConstraint": "eu-west-1"}
        )
        c.put_object(Bucket="probe-bucket", Key="k", Body=b"x")
        assert c.get_object(Bucket="probe-bucket", Key="k")["Body"].read() == b"x"

    def sqs():
        c = aws.client("sqs")
        u = c.create_queue(QueueName="p")["QueueUrl"]
        c.send_message(QueueUrl=u, MessageBody="m")
        assert c.receive_message(QueueUrl=u)["Messages"]

    def sns():
        c = aws.client("sns")
        c.publish(TopicArn=c.create_topic(Name="p")["TopicArn"], Message="m")

    def dynamodb():
        c = aws.client("dynamodb")
        c.create_table(
            TableName="p",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        c.put_item(TableName="p", Item={"id": {"S": "1"}})
        assert c.get_item(TableName="p", Key={"id": {"S": "1"}})["Item"]

    def kinesis():
        c = aws.client("kinesis")
        c.create_stream(StreamName="p", ShardCount=1)
        c.put_record(StreamName="p", Data=b"x", PartitionKey="k")

    def firehose():
        iam = aws.client("iam")
        role = iam.create_role(RoleName="fh", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
        aws.client("s3").create_bucket(
            Bucket="fh-bucket", CreateBucketConfiguration={"LocationConstraint": "eu-west-1"}
        )
        c = aws.client("firehose")
        c.create_delivery_stream(
            DeliveryStreamName="p",
            DeliveryStreamType="DirectPut",
            ExtendedS3DestinationConfiguration={
                "RoleARN": role,
                "BucketARN": "arn:aws:s3:::fh-bucket",
            },
        )
        c.put_record(DeliveryStreamName="p", Record={"Data": b"x\n"})
        assert aws.client("s3").list_objects_v2(Bucket="fh-bucket")["KeyCount"] >= 1

    def events():
        c = aws.client("events")
        c.put_rule(Name="p", EventPattern=json.dumps({"source": ["a"]}))
        c.put_events(Entries=[{"Source": "a", "DetailType": "t", "Detail": "{}"}])

    def secretsmanager():
        c = aws.client("secretsmanager")
        c.create_secret(Name="p", SecretString="s")
        assert c.get_secret_value(SecretId="p")["SecretString"] == "s"

    def ssm():
        c = aws.client("ssm")
        c.put_parameter(Name="/p", Value="v", Type="String")
        assert c.get_parameter(Name="/p")["Parameter"]["Value"] == "v"

    def kms():
        c = aws.client("kms")
        k = c.create_key()["KeyMetadata"]["KeyId"]
        ct = c.encrypt(KeyId=k, Plaintext=b"x")["CiphertextBlob"]
        assert c.decrypt(CiphertextBlob=ct)["Plaintext"] == b"x"

    def stepfunctions():
        c = aws.client("stepfunctions")
        iam = aws.client("iam")
        role = iam.create_role(RoleName="sf", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
        c.create_state_machine(
            name="p",
            definition=json.dumps({"StartAt": "A", "States": {"A": {"Type": "Pass", "End": True}}}),
            roleArn=role,
        )

    def _make_function():
        iam = aws.client("iam")
        role = iam.create_role(RoleName="lam", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("h.py", "def handler(e, c):\n    return 1\n")
        aws.client("lambda").create_function(
            FunctionName="p",
            Runtime="python3.12",
            Role=role,
            Handler="h.handler",
            Code={"ZipFile": buf.getvalue()},
        )

    def lambda_create():
        _make_function()

    def lambda_invoke():
        _make_function()
        r = aws.client("lambda").invoke(FunctionName="p", Payload=b"{}")
        assert (
            r["StatusCode"] == 200 and "FunctionError" not in r
        )  # moto runs real code only with Docker

    def athena():
        c = aws.client("athena")
        qid = c.start_query_execution(
            QueryString="select 1", ResultConfiguration={"OutputLocation": "s3://x/"}
        )["QueryExecutionId"]
        assert c.get_query_execution(QueryExecutionId=qid)

    def glue():
        c = aws.client("glue")
        c.create_database(DatabaseInput={"Name": "p"})
        assert c.get_database(Name="p")

    def organizations():
        c = aws.client("organizations", "us-east-1")
        c.create_organization(FeatureSet="ALL")

    all_ = {
        k: v
        for k, v in locals().items()
        if callable(v) and not k.startswith("_") and k not in ("aws", "json")
    }
    return all_


def run_probes(aws: MockAws) -> dict[str, dict]:
    out = {}
    for name, fn in probes(aws).items():
        aws.reset()
        ok, why = _ok(fn)
        out[name] = {"supported": ok, "detail": why}
    aws.reset()
    return out
