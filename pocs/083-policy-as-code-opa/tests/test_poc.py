import subprocess

import pytest
from policy_as_code_opa.engine import POLICY, OpaMissing, decide, decide_many, opa_bin, reference
from policy_as_code_opa.main import missing_attribute_report, run
from policy_as_code_opa.scenarios import SCENARIOS, random_requests, req

try:
    OPA = opa_bin()
except OpaMissing:
    pytest.skip("opa binary not installed (heavy test; CI installs it)", allow_module_level=True)

R = run(n_random=600)


def test_all_named_scenarios_match():
    bad = [s for s in R["scenarios"] if not s["ok"]]
    assert not bad, bad


def test_rego_matches_independent_python_reference_on_random_requests():
    assert R["differential_mismatches"] == 0
    assert 0 < R["random_allowed"] < R["random_requests"]  # both outcomes are exercised


def test_missing_attributes_fail_closed():
    assert missing_attribute_report() and R["missing_attribute_allowed"] == []


def test_rego_unit_tests_pass_and_policy_is_strict_clean():
    pol = POLICY.parent
    t = subprocess.run([OPA, "test", str(pol)], capture_output=True, text=True, timeout=60)
    assert t.returncode == 0, t.stdout + t.stderr
    c = subprocess.run(
        [OPA, "check", "--strict", str(pol)], capture_output=True, text=True, timeout=60
    )
    assert c.returncode == 0, c.stderr
    f = subprocess.run([OPA, "fmt", "-d", str(pol)], capture_output=True, text=True, timeout=60)
    assert f.stdout.strip() == "", "policy is not opa-fmt clean"


def test_single_decision_matches_batch():
    r = req(res={"pii": True}, ctx={"purpose": "marketing"})
    assert decide(r) == decide_many([r])[0]


def test_garbage_input_is_denied_not_crashed():
    for junk in ({}, {"user": "x"}, {"action": 5}, {"user": {"roles": "analyst"}}):
        assert decide(junk)["allow"] is False


def test_mutated_policy_is_caught_by_the_scenario_suite(tmp_path):
    """Delete the MFA deny: the suite must notice, otherwise it proves nothing."""
    src = POLICY.read_text()
    assert 'deny_reasons contains "mfa_required"' in src
    mutated = src.replace(
        'deny_reasons contains "mfa_required" if {',
        'deny_reasons contains "mfa_required" if {\n\tfalse',
    )
    p = tmp_path / "data_access.rego"
    p.write_text(mutated)
    got = decide_many([s[1] for s in SCENARIOS], policy=p)
    assert any(g["allow"] != s[2] for g, s in zip(got, SCENARIOS, strict=True))
    rr = random_requests(300)
    assert any(reference(r) != g for r, g in zip(rr, decide_many(rr, policy=p), strict=True))


def test_default_is_deny_with_explanation():
    d = decide(req({"roles": ["intern"]}))
    assert d == {"allow": False, "reasons": ["no_role_grants_action"]}
