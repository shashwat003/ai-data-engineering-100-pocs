"""Unit tests for the rules always run; Terraform and mock-AWS tests run when those tools exist."""

import copy
import json
from pathlib import Path

import pytest
from terraform_aws_datalake_foundation import inventory, mock_aws, rules, tfrun
from terraform_aws_datalake_foundation.inventory import Res

needs_tf = pytest.mark.skipif(not tfrun.available(), reason="terraform not installed")
needs_mock = pytest.mark.skipif(
    not (tfrun.available() and mock_aws.moto_available()),
    reason="terraform or moto[server] missing",
)
TAGS = {"Project": "p", "Environment": "dev", "ManagedBy": "terraform", "DataClassification": "x"}


def res(rtype, name="data", index="raw", unknown=None, **values):
    return Res(
        f'module.m.{rtype}.{name}["{index}"]', rtype, name, index, "module.m", values, unknown or {}
    )


def secure_bucket(index="raw", tags=None):
    return [
        res("aws_s3_bucket", index=index, tags=tags or TAGS),
        res("aws_s3_bucket_public_access_block", index=index, block_public_acls=True,
            block_public_policy=True, ignore_public_acls=True, restrict_public_buckets=True),
        res("aws_s3_bucket_server_side_encryption_configuration", index=index,
            rule=[{"bucket_key_enabled": True, "apply_server_side_encryption_by_default":
                   [{"sse_algorithm": "aws:kms", "kms_master_key_id": "arn:key"}]}]),
        res("aws_s3_bucket_versioning", index=index, versioning_configuration=[{"status": "Enabled"}]),
        res("aws_s3_bucket_ownership_controls", index=index, rule=[{"object_ownership": "BucketOwnerEnforced"}]),
        res("aws_s3_bucket_logging", index=index, target_bucket="logs"),
        res("aws_s3_bucket_lifecycle_configuration", index=index,
            rule=[{"abort_incomplete_multipart_upload": [{"days_after_initiation": 7}]}]),
    ]  # fmt: skip


def verdict(findings, rule):
    return {f.status for f in findings if f.rule == rule}


# ---- rules on hand-built inventories (no Terraform needed) ---------------------------------------


def test_secure_bucket_passes_every_bucket_rule():
    out = rules.evaluate(secure_bucket())
    bucket_rules = ["s3-public-access-blocked", "s3-encrypted-with-cmk", "s3-versioning-enabled",
                    "s3-acls-disabled", "s3-access-logging", "s3-lifecycle-cleanup", "required-tags"]  # fmt: skip
    for r in bucket_rules:
        assert verdict(out, r) == {rules.PASS}, r


@pytest.mark.parametrize(
    "mutate,rule",
    [
        (lambda r: r[1].values.update(block_public_acls=False), "s3-public-access-blocked"),
        (lambda r: r.pop(1), "s3-public-access-blocked"),
        (
            lambda r: (
                r[2]
                .values["rule"][0]["apply_server_side_encryption_by_default"][0]
                .update(sse_algorithm="AES256")
            ),
            "s3-encrypted-with-cmk",
        ),
        (lambda r: r.pop(2), "s3-encrypted-with-cmk"),
        (
            lambda r: r[3].values["versioning_configuration"][0].update(status="Suspended"),
            "s3-versioning-enabled",
        ),
        (
            lambda r: r[4].values["rule"][0].update(object_ownership="BucketOwnerPreferred"),
            "s3-acls-disabled",
        ),
        (lambda r: r.pop(5), "s3-access-logging"),
        (lambda r: r.pop(6), "s3-lifecycle-cleanup"),
        (lambda r: r[0].values.update(tags={"Project": "p"}), "required-tags"),
    ],
)
def test_each_misconfiguration_is_caught(mutate, rule):
    inv = secure_bucket()
    mutate(inv)
    assert verdict(rules.evaluate(inv), rule) == {rules.FAIL}


def test_log_bucket_may_use_sse_s3_but_a_data_bucket_may_not():
    logs = secure_bucket("logs", tags={**TAGS, "Layer": "logs"})
    logs[2].values["rule"][0]["apply_server_side_encryption_by_default"][0]["sse_algorithm"] = (
        "AES256"
    )
    assert verdict(rules.evaluate(logs), "s3-encrypted-with-cmk") == {rules.PASS}
    data = secure_bucket("raw")
    data[2].values["rule"][0]["apply_server_side_encryption_by_default"][0]["sse_algorithm"] = (
        "AES256"
    )
    assert verdict(rules.evaluate(data), "s3-encrypted-with-cmk") == {rules.FAIL}


def policy(*statements):
    return json.dumps({"Version": "2012-10-17", "Statement": list(statements)})


TLS = {"Effect": "Deny", "Action": "s3:*", "Condition": {"Bool": {"aws:SecureTransport": "false"}}}
ENC = {"Effect": "Deny", "Action": "s3:PutObject",
       "Condition": {"StringNotEquals": {"s3:x-amz-server-side-encryption": "aws:kms"}}}  # fmt: skip


def test_bucket_policy_rules_pass_fail_and_defer():
    ok = [*secure_bucket(), res("aws_s3_bucket_policy", policy=policy(TLS, ENC))]
    out = rules.evaluate(ok)
    assert verdict(out, "s3-tls-only") == {rules.PASS} and verdict(
        out, "s3-deny-unencrypted-put"
    ) == {rules.PASS}
    weak = [
        *secure_bucket(),
        res("aws_s3_bucket_policy", policy=policy({"Effect": "Allow", "Action": "s3:GetObject"})),
    ]
    out = rules.evaluate(weak)
    assert verdict(out, "s3-tls-only") == {rules.FAIL} and verdict(
        out, "s3-deny-unencrypted-put"
    ) == {rules.FAIL}
    unknown = [*secure_bucket(), res("aws_s3_bucket_policy", unknown={"policy": True})]
    out = rules.evaluate(unknown)
    assert verdict(out, "s3-tls-only") == {rules.DEFERRED}  # never silently passed
    assert verdict(rules.evaluate(secure_bucket()), "s3-tls-only") == {
        rules.FAIL
    }  # no policy at all


def test_iam_wildcards_and_open_trust_are_failures():
    bad_policy = res("aws_iam_role_policy", name="lake", index="analyst",
                     policy=policy({"Effect": "Allow", "Action": ["s3:*"], "Resource": "*"}))  # fmt: skip
    out = rules.evaluate([bad_policy])
    assert verdict(out, "iam-no-wildcards") == {rules.FAIL}
    good = res("aws_iam_role_policy", name="lake", index="analyst",
               policy=policy({"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": ["arn:aws:s3:::b/*"]},
                             {"Effect": "Deny", "Action": ["s3:DeleteObject", "s3:PutBucketPolicy", "kms:ScheduleKeyDeletion"],
                              "Resource": ["arn:aws:s3:::b"]}))  # fmt: skip
    assert verdict(rules.evaluate([good]), "iam-no-wildcards") == {rules.PASS}
    assert verdict(rules.evaluate([good]), "iam-explicit-denies") == {rules.PASS}
    open_trust = res("aws_iam_role", name="lake", index="x",
                     assume_role_policy=policy({"Effect": "Allow", "Principal": {"AWS": "*"}, "Action": "sts:AssumeRole"}))  # fmt: skip
    assert verdict(rules.evaluate([open_trust]), "iam-trust-not-open") == {rules.FAIL}


def test_least_privilege_flags_extra_actions_per_role():
    extra = res("aws_iam_role_policy", name="lake", index="analyst",
                policy=policy({"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"], "Resource": ["arn:aws:s3:::b/*"]}))  # fmt: skip
    f = [x for x in rules.evaluate([extra]) if x.rule == "iam-least-privilege"]
    assert f[0].status == rules.FAIL and "s3:PutObject" in f[0].detail


def test_kms_and_budget_rules():
    assert verdict(
        rules.evaluate([res("aws_kms_key", enable_key_rotation=False, deletion_window_in_days=30)]),
        "kms-rotation-enabled",
    ) == {rules.FAIL}
    assert verdict(
        rules.evaluate([res("aws_kms_key", enable_key_rotation=True, deletion_window_in_days=3)]),
        "kms-deletion-window",
    ) == {rules.FAIL}
    assert verdict(rules.evaluate([]), "budget-guard") == {rules.FAIL}
    assert verdict(
        rules.evaluate([res("aws_budgets_budget", notification=[{"threshold": 80}])]),
        "budget-guard",
    ) == {rules.PASS}


# ---- real Terraform: formatting, validation, variable checks, plans -------------------------------


@pytest.fixture(scope="session")
def ws(tmp_path_factory):
    infra = tfrun.workspace(tmp_path_factory.mktemp("tf"))
    for env in ("envs/dev", "envs/local-mock", "examples/insecure"):
        tfrun.init(infra / env)
    return infra


@needs_tf
def test_fmt_and_validate(ws):
    assert tfrun.tf(["fmt", "-check", "-recursive", "-no-color"], ws, check=False).returncode == 0
    for env in ("envs/dev", "envs/local-mock", "examples/insecure"):
        assert "valid" in tfrun.tf(["validate", "-no-color"], ws / env).stdout


@needs_tf
@pytest.mark.parametrize(
    "var,expected",
    [
        ("account_id=abc", "12 digits"),
        ("kms_deletion_window_days=3", "between 7 and 30"),
        ("ingest_principal_arns=[]", "at least one IAM principal"),
        ('analyst_principal_arns=["*"]', "at least one IAM principal"),
        ('name_prefix="Bad_Name"', "lowercase"),
        ('environment="qa"', "dev, staging or prod"),
        ("monthly_budget_usd=0", "positive"),
    ],
)
def test_input_validation_rejects_unsafe_values(ws, tmp_path, var, expected):
    name, value = var.split("=", 1)
    d = ws / "envs/local-mock"
    main = (d / "main.tf").read_text()
    # override one module argument by rewriting the module call in a scratch copy
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    import re

    if name in ("account_id", "name_prefix", "environment"):
        main = re.sub(
            rf"{name}\s*=\s*\"[^\"]*\"",
            f"{name} = {value if value.startswith('"') else json.dumps(value)}",
            main,
            count=1,
        )
    else:
        main = (
            main.replace('module "datalake" {', f'module "datalake" {{\n  {name} = {value}', 1)
            if name not in ("ingest_principal_arns", "analyst_principal_arns")
            else re.sub(rf"{name}\s*=\s*\[[^\]]*\]", f"{name} = {value}", main, count=1)
        )
    (scratch / "main.tf").write_text(
        main.replace("../../modules/datalake", str((ws / "modules/datalake").resolve()))
    )
    tfrun.tf(["init", "-backend=false", "-no-color"], scratch)
    proc = tfrun.tf(["plan", "-no-color", "-lock=false"], scratch, check=False)
    assert proc.returncode != 0 and expected in " ".join(proc.stderr.split())


@pytest.fixture(scope="session")
def plan_findings(ws):
    plan = tfrun.plan_json(ws / "envs/local-mock")
    return plan, rules.evaluate(inventory.from_plan(plan))


@needs_tf
def test_module_plan_has_no_failures_and_defers_only_policy_documents(plan_findings):
    _, findings = plan_findings
    assert [f for f in findings if f.status == rules.FAIL] == []
    assert {f.rule for f in findings if f.status == rules.DEFERRED} <= {
        "s3-tls-only", "s3-deny-unencrypted-put", "iam-no-wildcards", "iam-trust-not-open"}  # fmt: skip
    assert sum(f.status == rules.PASS for f in findings) >= 30


@needs_tf
def test_plan_creates_the_expected_resources_and_nothing_else(plan_findings):
    plan, _ = plan_findings
    kinds = {}
    for rc in (r for r in plan["resource_changes"] if r["mode"] == "managed"):
        assert rc["change"]["actions"] == ["create"]
        kinds[rc["type"]] = kinds.get(rc["type"], 0) + 1
    assert kinds["aws_s3_bucket"] == 3 and kinds["aws_kms_key"] == 1 and kinds["aws_iam_role"] == 3
    assert kinds["aws_s3_bucket_public_access_block"] == 3 and kinds["aws_budgets_budget"] == 1
    paid = {"aws_instance", "aws_nat_gateway", "aws_rds_cluster", "aws_db_instance", "aws_eip",
            "aws_redshift_cluster", "aws_emr_cluster", "aws_glue_job"}  # fmt: skip
    assert not paid & set(kinds)


@needs_tf
def test_insecure_example_is_flagged_by_the_checker(ws):
    findings = rules.evaluate(inventory.from_plan(tfrun.plan_json(ws / "examples/insecure")))
    failed = {f.rule for f in findings if f.status == rules.FAIL}
    assert {"s3-public-access-blocked", "s3-encrypted-with-cmk", "s3-versioning-enabled",
            "kms-rotation-enabled", "iam-no-wildcards", "iam-trust-not-open", "required-tags",
            "budget-guard"} <= failed  # fmt: skip


@needs_tf
def test_mutating_a_real_plan_makes_the_checker_fail(plan_findings):
    plan, _ = plan_findings
    mutated = copy.deepcopy(plan)
    for rc in mutated["resource_changes"]:
        if rc["type"] == "aws_s3_bucket_public_access_block" and rc.get("index") == "raw":
            rc["change"]["after"]["block_public_policy"] = False
        if rc["type"] == "aws_kms_key":
            rc["change"]["after"]["enable_key_rotation"] = False
    failed = {
        (f.rule, f.address)
        for f in rules.evaluate(inventory.from_plan(mutated))
        if f.status == rules.FAIL
    }
    assert ("s3-public-access-blocked", 'module.datalake.aws_s3_bucket.data["raw"]') in failed
    assert any(r == "kms-rotation-enabled" for r, _ in failed)


def test_repository_never_applies_to_real_aws():
    root = Path(__file__).resolve().parent.parent
    text = (root / "Makefile").read_text()
    assert "terraform apply" not in text and "terraform destroy" not in text
    mock = (root / "infra/envs/local-mock/main.tf").read_text()
    assert '"mock"' in mock and "127.0.0.1" in mock and "skip_credentials_validation = true" in mock
    for tf_file in (root / "infra").rglob("*.tf"):
        body = tf_file.read_text()
        assert "AKIA" not in body and "aws_secret_access_key" not in body.lower()


# ---- apply to a mock AWS and inspect what was actually created ------------------------------------


@pytest.fixture(scope="session")
def applied(ws):
    if not (tfrun.available() and mock_aws.moto_available()):
        pytest.skip("terraform or moto[server] missing")
    d = ws / "envs/local-mock"
    with mock_aws.moto_server() as port:
        mock_aws.apply(d, port)
        try:
            yield d, port, tfrun.state_json(d)
        finally:
            mock_aws.destroy(d, port)


@needs_mock
def test_applied_state_passes_every_rule_with_nothing_deferred(applied):
    _, _, state = applied
    findings = [f for f in rules.evaluate(inventory.from_state(state)) if f.rule != "budget-guard"]
    assert rules.summarise(findings)[rules.FAIL] == 0, [
        f for f in findings if f.status == rules.FAIL
    ]
    assert rules.summarise(findings)[rules.DEFERRED] == 0
    assert {f.rule for f in findings} >= {
        "iam-least-privilege",
        "s3-tls-only",
        "iam-explicit-denies",
    }


@needs_mock
def test_live_api_shows_encryption_public_block_versioning_and_rotation(applied):
    _, port, _ = applied
    s3, kms = mock_aws.client("s3", port), mock_aws.client("kms", port)
    names = {b["Name"] for b in s3.list_buckets()["Buckets"]}
    assert names == {f"mock-data-dev-123456789012-{x}" for x in ("raw", "curated", "logs")}
    key_arn = None
    for n in names:
        pab = s3.get_public_access_block(Bucket=n)["PublicAccessBlockConfiguration"]
        assert all(pab.values()) and len(pab) == 4
        assert s3.get_bucket_versioning(Bucket=n)["Status"] == "Enabled"
        sse = s3.get_bucket_encryption(Bucket=n)["ServerSideEncryptionConfiguration"]["Rules"][0]
        algo = sse["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]
        if n.endswith("-logs"):
            assert algo == "AES256"
        else:
            assert algo == "aws:kms" and sse["BucketKeyEnabled"] is True
            key_arn = sse["ApplyServerSideEncryptionByDefault"]["KMSMasterKeyID"]
    key_id = key_arn.split("/")[-1]
    assert kms.get_key_rotation_status(KeyId=key_id)["KeyRotationEnabled"] is True
    assert kms.describe_key(KeyId=key_id)["KeyMetadata"]["Enabled"] is True


@needs_mock
def test_live_bucket_policy_pins_the_lake_key_and_requires_tls(applied):
    _, port, _ = applied
    s3 = mock_aws.client("s3", port)
    doc = json.loads(s3.get_bucket_policy(Bucket="mock-data-dev-123456789012-raw")["Policy"])
    sids = {s["Sid"] for s in doc["Statement"]}
    assert sids == {"DenyInsecureTransport", "DenyUnencryptedUploads", "DenyOtherKmsKeys"}
    assert all(s["Effect"] == "Deny" for s in doc["Statement"])


@needs_mock
def test_live_iam_roles_match_least_privilege(applied):
    _, port, _ = applied
    iam = mock_aws.client("iam", port)
    for role, allowed in rules.ROLE_ALLOWED.items():
        name = f"mock-data-dev-{role}"
        doc = iam.get_role_policy(RoleName=name, PolicyName="least-privilege")["PolicyDocument"]
        granted = {
            a
            for s in doc["Statement"]
            if s["Effect"] == "Allow"
            for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])
        }
        assert granted == allowed, role
    trust = iam.get_role(RoleName="mock-data-dev-analyst")["Role"]["AssumeRolePolicyDocument"]
    assert trust["Statement"][0]["Condition"]["Bool"]["aws:MultiFactorAuthPresent"] == "true"


@needs_mock
def test_second_plan_after_apply_shows_no_drift(applied):
    d, port, _ = applied
    assert mock_aws.drift(d, port) == 0


@needs_mock
def test_destroy_removes_everything(ws):
    d = ws / "envs/local-mock"
    with mock_aws.moto_server() as port:
        mock_aws.apply(d, port)
        mock_aws.destroy(d, port)
        assert mock_aws.client("s3", port).list_buckets()["Buckets"] == []
