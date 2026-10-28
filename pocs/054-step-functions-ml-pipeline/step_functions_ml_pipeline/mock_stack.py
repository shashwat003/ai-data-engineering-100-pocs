"""Deploy the definition to a local moto Step Functions with boto3 and read it back. Never touches real AWS.

Terraform was tried first and abandoned: the AWS provider calls `ValidateStateMachineDefinition` at plan time, which the mock does
not implement, so `terraform plan/apply` cannot run against it. Deployment here is therefore boto3, not IaC.
"""

from __future__ import annotations

import json

from .asl import validate
from .pipeline import DEFINITION

ASSUME = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "states.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
)


def deploy_and_inspect(
    aws, definition: dict | None = None, name: str = "ml-training-pipeline"
) -> dict:
    definition = definition or DEFINITION
    errs = validate(definition)
    if errs:
        raise ValueError(f"refusing to deploy an invalid definition: {errs}")
    role = aws.client("iam").create_role(
        RoleName="ml-pipeline-sfn", AssumeRolePolicyDocument=ASSUME
    )["Role"]["Arn"]
    sfn = aws.client("stepfunctions")
    arn = sfn.create_state_machine(
        name=name,
        definition=json.dumps(definition),
        roleArn=role,
        type="STANDARD",
        loggingConfiguration={"level": "ERROR", "includeExecutionData": False},
    )["stateMachineArn"]
    d = sfn.describe_state_machine(stateMachineArn=arn)
    out = {
        "stored_definition_matches": json.loads(d["definition"]) == definition,
        "type": d["type"],
        "logging_level": d.get("loggingConfiguration", {}).get("level"),
        "listed": [m["name"] for m in sfn.list_state_machines()["stateMachines"]],
    }
    changed = {**definition, "Comment": "updated"}
    sfn.update_state_machine(stateMachineArn=arn, definition=json.dumps(changed))
    out["update_roundtrip"] = (
        json.loads(sfn.describe_state_machine(stateMachineArn=arn)["definition"])["Comment"]
        == "updated"
    )
    return out
