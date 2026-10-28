import json
import shutil
from pathlib import Path

import pytest
from fargate_bluegreen_api.bluegreen import (
    STEPS,
    InvariantViolation,
    World,
    check_invariants,
    deploy,
)
from fargate_bluegreen_api.main import SCENARIOS, run, simulate, terraform_plan
from fargate_bluegreen_api.oidc import lint_permissions, lint_trust_policy
from fargate_bluegreen_api.plan_checks import check_plan
from fargate_bluegreen_api.workflow_lint import lint_workflow

FIX = Path(__file__).resolve().parent.parent / "fixtures"


def load(kind, name):
    return (FIX / kind / name).read_text()


# --- OIDC trust --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["good_env", "good_branch"])
def test_good_trust_policies_have_no_findings(name):
    assert (
        lint_trust_policy(json.loads(load("trust", f"{name}.json")), account_id="123456789012")
        == []
    )


@pytest.mark.parametrize(
    "name,needle",
    [
        ("bad_no_sub", "ANY repository"),
        ("bad_no_aud", "audience unchecked"),
        ("bad_wildcard_owner", "too broad"),
        ("bad_any_ref", "too broad"),
        ("bad_star", "too broad"),
        ("bad_pull_request", "pull-request"),
        ("bad_principal_star", "anyone can assume"),
        ("bad_wrong_action", "AssumeRoleWithWebIdentity"),
        ("bad_no_conditions", "ANY repository"),
        ("bad_other_account_provider", "different account"),
        ("bad_wrong_audience", "audience must be exactly"),
    ],
)
def test_each_bad_trust_policy_is_flagged_for_the_right_reason(name, needle):
    msgs = lint_trust_policy(json.loads(load("trust", f"{name}.json")), account_id="123456789012")
    assert any(needle in m for m in msgs), msgs


def test_trust_policy_edge_cases():
    assert lint_trust_policy({"Statement": []}) == ["trust policy has no statements"]
    assert (
        lint_trust_policy('{"Statement": [{"Effect": "Deny", "Principal": "*"}]}') == []
    )  # a Deny never grants access
    assert lint_trust_policy(
        {"Statement": [{"Effect": "Allow", "Principal": {"AWS": "*"}, "Action": "sts:AssumeRole"}]}
    )


# --- permissions ---------------------------------------------------------------------------------------------------
def test_good_permissions_clean():
    assert lint_permissions(json.loads(load("permissions", "good.json"))) == []


@pytest.mark.parametrize(
    "name,needle",
    [
        ("bad_passrole_star", "privilege escalation"),
        ("bad_passrole_no_condition", "PassedToService"),
        ("bad_admin", "wildcard action"),
        ("bad_service_wildcard", "wildcard action"),
        ("bad_ecr_everywhere", "resource-scopable"),
        ("bad_iam_write", "should not use"),
    ],
)
def test_bad_permissions_flagged(name, needle):
    assert any(
        needle in m for m in lint_permissions(json.loads(load("permissions", f"{name}.json")))
    )


# --- workflows -----------------------------------------------------------------------------------------------------
def test_good_workflow_clean():
    assert lint_workflow(load("workflows", "good.yml")) == []


@pytest.mark.parametrize(
    "name,needle",
    [
        ("bad_tag_pins", "not pinned to a commit SHA"),
        ("bad_static_keys", "long-lived AWS credentials"),
        ("bad_pull_request_target", "untrusted context"),
        ("bad_no_environment", "protected `environment`"),
        ("bad_no_id_token", "lacks `id-token: write`"),
        ("bad_script_injection", "script injection"),
        ("bad_write_all", "write access to every job"),
        ("bad_no_timeout_no_concurrency", "concurrency"),
        ("bad_unneeded_id_token", "unneeded permission"),
    ],
)
def test_bad_workflows_flagged(name, needle):
    assert any(needle in m for m in lint_workflow(load("workflows", f"{name}.yml")))


def test_workflow_garbage_inputs():
    assert lint_workflow("a: [")[0].startswith("invalid YAML")
    assert lint_workflow("- 1")[0].startswith("not a workflow")


# --- blue/green simulation -----------------------------------------------------------------------------------------
def test_invariants_detect_violations():
    w = World()
    w.weights = (50, 50)
    with pytest.raises(InvariantViolation, match="green with no healthy"):
        check_invariants(w)
    w.weights = (60, 50)
    with pytest.raises(InvariantViolation, match="sum"):
        check_invariants(w)
    w.weights, w.blue_healthy = (100, 0), 0
    with pytest.raises(InvariantViolation, match="blue"):
        check_invariants(w)


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_no_invariant_violation_in_any_scenario_or_seed(name):
    cfg = {k: v for k, v in SCENARIOS[name].items() if not k.startswith("_")}
    for seed in range(12):
        out = deploy(World(seed=seed, **cfg))  # raises InvariantViolation on any breach
        assert out["result"] in ("completed", "rolled_back")
        if out["result"] == "rolled_back":
            assert (
                out["final_weights"] == (100, 0)
                and out["blue_healthy"] > 0
                and out["green_healthy"] == 0
            )
        else:
            assert (
                out["final_weights"] == (0, 100)
                and out["blue_healthy"] == 0
                and out["green_healthy"] >= 4
            )


def test_bad_release_is_rolled_back_before_full_traffic():
    outs = [deploy(World(seed=s, green_error_rate=0.05)) for s in range(15)]
    assert all(o["result"] == "rolled_back" for o in outs) and all(
        "lower bound" in o["reason"] for o in outs
    )
    assert (
        sum(o["bad_requests_served_by_green"] for o in outs) / 15 < 60
    )  # a small blast radius compared with 5% of all traffic for a full window


def test_tasks_that_never_start_trip_the_circuit_breaker_without_traffic_shift():
    out = deploy(World(seed=1, green_start_fail_rate=1.0))
    assert (
        out["result"] == "rolled_back"
        and "circuit breaker" in out["reason"]
        and out["bad_requests_served_by_green"] == 0
    )


def test_tolerable_difference_does_not_roll_back():
    outs = [deploy(World(seed=s, green_error_rate=0.010)) for s in range(15)]
    assert sum(o["result"] == "completed" for o in outs) >= 13


def test_controller_crash_and_resume_gives_the_same_outcome():
    for seed in range(5):
        assert deploy(World(seed=seed), crash_at=8) == deploy(World(seed=seed))


def test_steps_end_at_full_cutover():
    assert STEPS[-1] == 100 and list(STEPS) == sorted(STEPS)


def test_deadline_fails_closed():
    out = deploy(World(seed=0), max_ticks=3)
    assert (
        out["result"] == "rolled_back"
        and "deadline" in out["reason"]
        and out["final_weights"] == (100, 0)
    )


def test_simulation_summary_shape():
    s = simulate(seeds=5)
    assert (
        s["healthy release"]["completed"] == 5 and s["green returns 5xx (5%)"]["rolled_back"] == 5
    )


# --- terraform plan (offline) -------------------------------------------------------------------------------------
needs_tf = pytest.mark.skipif(
    shutil.which("terraform") is None, reason="terraform required (heavy CI job)"
)


@needs_tf
def test_clean_plan_passes_all_checks_and_unsafe_variants_fail():
    assert check_plan(terraform_plan()) == []
    assert any(
        "not pinned by digest" in m
        for m in check_plan(terraform_plan(["-var", "image=x.example.com/app:latest"]))
    )
    assert any(
        "too broad" in m
        for m in check_plan(terraform_plan(["-var", "deploy_subject=repo:example-org/*"]))
    )


def test_plan_checks_on_handmade_plans():
    def plan(**after):
        return {
            "resource_changes": [
                {"type": t, "change": {"actions": ["create"], "after": a}} for t, a in after.items()
            ]
        }

    bad_svc = {
        "deployment_circuit_breaker": [{"enable": False, "rollback": False}],
        "desired_count": 1,
        "deployment_minimum_healthy_percent": 50,
        "network_configuration": [{"assign_public_ip": True, "subnets": ["s1"]}],
    }
    msgs = check_plan(plan(aws_ecs_service=bad_svc))
    for needle in (
        "circuit breaker",
        "desired_count",
        "minimum healthy",
        "public IPs",
        "single subnet",
    ):
        assert any(needle in m for m in msgs), needle
    td = {
        "container_definitions": json.dumps(
            [{"name": "a", "image": "x:latest", "privileged": True, "user": "root"}]
        )
    }
    msgs = check_plan(plan(aws_ecs_task_definition=td))
    for needle in (
        "digest",
        "writable",
        "privileged",
        "log configuration",
        "root",
        "execution role",
    ):
        assert any(needle in m for m in msgs), needle
    lis = {"protocol": "HTTP", "default_action": [{"type": "forward"}]}
    assert any("redirecting" in m for m in check_plan(plan(aws_lb_listener=lis)))
    skew = {
        "protocol": "HTTPS",
        "ssl_policy": "ELBSecurityPolicy-TLS13-1-2-2021-06",
        "default_action": [
            {"type": "forward", "forward": [{"target_group": [{"weight": 60}, {"weight": 30}]}]}
        ],
    }
    assert any("sum to 90" in m for m in check_plan(plan(aws_lb_listener=skew)))


def test_demo_runs_without_terraform():
    r = run(with_terraform=False)
    assert all(
        n == 0 for k, rows in r["corpus"].items() for name, n in rows if name.startswith("good")
    )
    assert all(
        n > 0 for k, rows in r["corpus"].items() for name, n in rows if name.startswith("bad")
    )
    assert r["terraform"]["status"] == "SKIPPED"
