import json

import pytest
from landing_zone_guardrails.lint import lint_org, lint_policy
from landing_zone_guardrails.matrix import load_cases, mutation_report, run_matrix
from landing_zone_guardrails.simulator import (
    FULL_ACCESS,
    Node,
    Organisation,
    Request,
    Unsupported,
    statement_matches,
)
from landing_zone_guardrails.tooling import POC, moto_available, terraform_available

BG = "OrganizationBreakGlass"


def req(
    action="s3:GetObject",
    ctx=None,
    principal="arn:aws:iam::1:role/x",
    account="111111111111",
    resource="*",
):
    return Request(principal, account, action, resource, tuple(sorted((ctx or {}).items())))


def org_with(
    *policies: dict, root=("FullAWSAccess",), ou_policies=("FullAWSAccess",)
) -> Organisation:
    pols = {f"p{i}": p for i, p in enumerate(policies)}
    return Organisation(
        Node("root", list(root), [Node("ou", list(ou_policies), [], ["111111111111"])], []),
        pols,
        "999999999999",
    )


def deny(**kw):
    return {
        "Version": "2012-10-17",
        "Statement": [{"Sid": "S", "Effect": "Deny", "Resource": "*", **kw}],
    }


@pytest.fixture(scope="module")
def real_org():
    return Organisation.load(POC / "org.json", POC / "policies")


def test_condition_semantics_for_missing_keys():
    neg = {
        "Effect": "Deny",
        "Action": "*",
        "Resource": "*",
        "Condition": {"StringNotEquals": {"k": "v"}},
    }
    pos = {
        "Effect": "Deny",
        "Action": "*",
        "Resource": "*",
        "Condition": {"StringEquals": {"k": "v"}},
    }
    assert (
        statement_matches(neg, req())
        and not statement_matches(neg, req(ctx={"k": "v"}))
        and statement_matches(neg, req(ctx={"k": "z"}))
    )
    assert not statement_matches(pos, req()) and statement_matches(pos, req(ctx={"k": "v"}))


def test_null_bool_and_multi_value_semantics():
    null = {"Effect": "Deny", "Action": "*", "Condition": {"Null": {"k": "true"}}}
    assert statement_matches(null, req()) and not statement_matches(null, req(ctx={"k": "x"}))
    b = {"Effect": "Deny", "Action": "*", "Condition": {"Bool": {"flag": "true"}}}
    assert statement_matches(b, req(ctx={"flag": "TRUE"})) and not statement_matches(b, req())
    multi = {"Effect": "Deny", "Action": "*", "Condition": {"StringNotEquals": {"r": ["a", "b"]}}}
    assert not statement_matches(multi, req(ctx={"r": "b"})) and statement_matches(
        multi, req(ctx={"r": "c"})
    )
    both = {
        "Effect": "Deny",
        "Action": "*",
        "Condition": {"StringEquals": {"a": "1"}, "Null": {"b": "true"}},
    }
    assert statement_matches(both, req(ctx={"a": "1"})) and not statement_matches(
        both, req(ctx={"a": "1", "b": "x"})
    )


def test_action_and_resource_matching():
    st = {"Effect": "Deny", "NotAction": ["iam:*"], "NotResource": "arn:aws:s3:::safe/*"}
    assert statement_matches(st, req("s3:GetObject", resource="arn:aws:s3:::other/k"))
    assert not statement_matches(st, req("s3:GetObject", resource="arn:aws:s3:::safe/k"))
    assert not statement_matches(st, req("IAM:CreateRole"))


@pytest.mark.parametrize(
    "st",
    [
        {"Effect": "Deny", "Action": "*", "Principal": "*"},
        {"Effect": "Deny", "Action": "*", "Condition": {"IpAddress": {"aws:SourceIp": "1.2.3.4"}}},
        {"Effect": "Deny", "Action": "*", "Condition": {"StringEquals": {"k": "${aws:username}"}}},
    ],
)
def test_unsupported_features_fail_closed_by_raising(st):
    with pytest.raises(Unsupported):
        statement_matches(st, req())


def test_allow_needed_at_every_level_and_explicit_deny_wins():
    allow_s3 = {
        "Version": "2012-10-17",
        "Statement": [{"Sid": "A", "Effect": "Allow", "Action": "s3:*", "Resource": "*"}],
    }
    o = org_with(allow_s3, root=("FullAWSAccess",), ou_policies=("p0",))
    assert (
        o.evaluate(req("s3:GetObject")).allowed
        and "implicit" in o.evaluate(req("ec2:RunInstances")).reason
    )
    o2 = org_with(
        allow_s3, deny(Action="s3:GetObject"), root=("FullAWSAccess", "p1"), ou_policies=("p0",)
    )
    d = o2.evaluate(req("s3:GetObject"))
    assert not d.allowed and "explicit deny" in d.reason
    assert (
        not org_with(root=(), ou_policies=("FullAWSAccess",)).evaluate(req()).allowed
    )  # root without any allow


def test_exemptions():
    o = org_with(deny(Action="*"), root=("FullAWSAccess", "p0"))
    assert not o.evaluate(req()).allowed
    assert o.evaluate(req(account="999999999999")).allowed
    assert o.evaluate(req(principal="arn:aws:iam::1:role/aws-service-role/x/y")).allowed


def test_full_access_constant_is_allow_all():
    assert FULL_ACCESS["Statement"][0]["Action"] == "*"


def test_regression_matrix_all_hold(real_org):
    res = run_matrix(real_org)
    assert len(res) >= 28 and [r for r in res if not r["ok"]] == []


def test_every_guardrail_is_protected_by_at_least_one_case(real_org):
    rep = mutation_report(real_org)
    assert all(rep[p] for p in rep), {p: v for p, v in rep.items() if not v}


def test_matrix_catches_a_weakened_policy(real_org):
    weak = json.loads(json.dumps(real_org.policies["region-lock"]))
    weak["Statement"][0]["Condition"]["StringNotEquals"]["aws:RequestedRegion"].append("us-east-1")
    o = Organisation(real_org.tree, {**real_org.policies, "region-lock": weak}, real_org.management)
    failed = {r["id"] for r in run_matrix(o) if not r["ok"]}
    assert {"prod-read-wrong-region", "action-case-insensitive"} <= failed


def test_expectations_are_independent_of_simulator():
    c = load_cases()
    assert {x["expect"] for x in c} == {"allow", "deny"} and len({x["id"] for x in c}) == len(c)


def test_lint_clean_for_repo_policies(real_org):
    assert lint_org(real_org, BG) == []


@pytest.mark.parametrize(
    "doc,needle",
    [
        (
            {
                "Version": "2012-10-17",
                "Statement": [
                    {"Sid": "A", "Effect": "Allow", "NotAction": "iam:*", "Resource": "*"}
                ],
            },
            "NotAction",
        ),
        (
            {
                "Version": "2012-10-17",
                "Statement": [{"Sid": "A", "Effect": "Deny", "Action": "ec2:*", "Resource": "*"}],
            },
            "lock operators out",
        ),
        (
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Deny",
                        "Action": "ec2:*",
                        "Resource": "*",
                        "Condition": {"Null": {"a": "true"}},
                    }
                ],
            },
            "unique Sid",
        ),
        ({"Version": "2008-10-17", "Statement": []}, "Version"),
        (
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "A",
                        "Effect": "Deny",
                        "Action": "x:*",
                        "Resource": "*",
                        "Condition": {"IpAddress": {"a": "b"}},
                    }
                ],
            },
            "not supported",
        ),
        (
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": f"S{i}",
                        "Effect": "Deny",
                        "Action": "x:" + "a" * 60,
                        "Resource": "*",
                        "Condition": {"Null": {"k": "true"}},
                    }
                    for i in range(80)
                ],
            },
            "5120",
        ),
    ],
)
def test_lint_flags_each_problem(doc, needle):
    assert any(needle in m for m in lint_policy("p", doc, BG))


def test_lint_org_structure_problems(real_org):
    o = real_org.with_attachments(
        {
            "root": ["deny-leave-org", "ghost"],
            "prod": ["FullAWSAccess"] * 6,
            "sandbox": ["FullAWSAccess"],
            "regulated": ["FullAWSAccess"],
        }
    )
    msgs = lint_org(o, BG)
    assert (
        any("ghost" in m for m in msgs)
        and any("more than 5" in m for m in msgs)
        and any("FullAWSAccess detached" in m for m in msgs)
    )


def test_actual_state_with_default_attachment_breaks_allow_list(real_org):
    actual = real_org.with_attachments(
        {
            "root": ["FullAWSAccess"],
            "prod": ["FullAWSAccess"],
            "sandbox": ["FullAWSAccess"],
            "regulated": ["FullAWSAccess", "regulated-allowlist"],
        }
    )
    d = actual.evaluate(
        req("ec2:RunInstances", {"aws:RequestedRegion": "eu-west-1"}, account="444444444444")
    )
    assert (
        d.allowed
    )  # the allow-list is ineffective while the default FullAWSAccess is still attached


needs_tf = pytest.mark.skipif(
    not (terraform_available() and moto_available()),
    reason="terraform and moto are required (heavy CI job)",
)


@needs_tf
def test_terraform_apply_to_mock_reconcile_and_drift():
    from landing_zone_guardrails.main import terraform_section

    t = terraform_section()
    assert t["content_matches_files"] and t["required_config_rules_present"]
    assert t["attachments_match_org_json_after_apply"] is False and t["reconciled_ous"] == [
        "regulated"
    ]
    assert (
        t["attachments_match_org_json_after_reconcile"] is True
        and t["matrix_on_actual_state_after_reconcile"].endswith("/28")
        and t["matrix_on_actual_state_after_reconcile"][:2] == "28"
    )
    assert t["allowlist_gap_cases_after_apply"] and t["clean_replan_exit_code"] == 0
    assert (
        t["drift_plan_exit_code_after_console_edit"] == 2 and t["plan_exit_code_after_reapply"] == 0
    )
