import os
import random
import stat
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest
import yaml
from secrets_scanning_kit import gitleaks
from secrets_scanning_kit.cli import main as cli_main
from secrets_scanning_kit.corpus import negatives, positives
from secrets_scanning_kit.evaluate import run
from secrets_scanning_kit.scanner import (
    entropy,
    load_baseline,
    new_findings,
    scan_file,
    scan_text,
    write_baseline,
)

KIT = Path(__file__).resolve().parent.parent / "kit"
POC = Path(__file__).resolve().parent.parent
RES = run()
KS = RES["tools"]["kit scanner"]


def test_every_planted_secret_is_found_and_no_lookalike_is_flagged():
    assert KS["recall"] == 1.0 and KS["fp_rate"] == 0.0
    rng = random.Random(1)
    for typ, name, content in positives(rng):
        rules = {f.rule for f in scan_text(content, name)}
        assert typ in rules, (typ, name)
    for name, content in negatives(random.Random(1)):
        assert scan_text(content, name) == [], name


def test_holdout_gaps_are_pinned_so_improvements_and_regressions_are_visible():
    assert set(KS["holdout_missed"]) == {
        "azure-connection-string",
        "bearer-header",
        "private-key-body",
        "split-token",
    }
    assert KS["holdout_recall"] == pytest.approx(3 / 7, abs=0.01)


def test_findings_never_contain_the_secret():
    secret = "gh" + "p_" + "A1b2C3d4E5" * 3 + "xyzxyz"
    f = scan_text(f"token = '{secret}'\n", "x.py")[0]
    assert secret not in repr(f) and secret not in f.preview and f.preview.startswith("ghp_")
    assert secret[:12] not in f.fingerprint


def test_fingerprint_is_stable_and_differs_per_value():
    a, b = "gh" + "p_" + "a" * 36, "gh" + "p_" + "b" * 36
    assert scan_text(a)[0].fingerprint == scan_text(a)[0].fingerprint != scan_text(b)[0].fingerprint


def test_allowlist_pragmas_and_placeholders():
    k = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
    assert (
        scan_text(f"K='{k}'")
        and not scan_text(f"K='{k}'  # pragma: allowlist secret")
        and not scan_text(f"K='{k}'  # gitleaks:allow")
    )
    assert scan_text("AKIAIOSFODNN7" + "EXAMPLE") == []
    assert (
        scan_text("password = ${DB_PASSWORD_FROM_ENV_VARIABLE}") == []
        and scan_text("api_key = '" + "your-api-key-" + "goes-here-xxxx'") == []
    )


def test_entropy_function():
    assert (
        entropy("") == 0
        and entropy("aaaa") == 0
        and entropy("abcdefgh") == pytest.approx(3.0)
        and entropy("aA1+bB2/cC3=dD4-") > 3.9
    )


def test_binary_oversized_and_pathological_inputs_are_safe(tmp_path):
    (tmp_path / "bin").write_bytes(b"\x00\x01" + b"password='" + b"A1b2" * 10 + b"'")
    (tmp_path / "big.txt").write_text("x" * 1_100_000)
    (tmp_path / "evil.txt").write_text("a=" * 400_000)  # long line: skipped, and must not hang
    t0 = time.perf_counter()
    assert [scan_file(tmp_path / n) for n in ("bin", "big.txt", "evil.txt")] == [[], [], []]
    assert time.perf_counter() - t0 < 2
    t0 = time.perf_counter()
    scan_text("token=" + "A" * 4000)
    scan_text("secret = '" + "ab/" * 1500 + "'")
    assert time.perf_counter() - t0 < 1


def test_baseline_accepts_old_findings_and_flags_new_ones(tmp_path):
    old = "gh" + "p_" + "c" * 36
    (tmp_path / "a.py").write_text(f"T='{old}'\n")
    base = tmp_path / ".base.json"
    assert cli_main(["--repo", str(tmp_path), "--baseline", str(base), "--write-baseline"]) == 0
    assert cli_main(["--repo", str(tmp_path), "--baseline", str(base)]) == 0
    (tmp_path / "b.py").write_text("T='" + "gh" + "p_" + "d" * 36 + "'\n")
    assert cli_main(["--repo", str(tmp_path), "--baseline", str(base)]) == 1
    f = scan_file(tmp_path / "a.py", tmp_path)
    assert new_findings(f, load_baseline(base)) == [] and write_baseline(base, f) is None


def git(repo, *args, env=None):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            **(env or {}),
        },
        check=False,
    )


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" -m secrets_scanning_kit.cli --repo "$(git rev-parse --show-toplevel)" --staged\n'
    )
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    return tmp_path


def test_git_pre_commit_hook_blocks_secrets_and_stays_quiet_about_their_value(repo):
    env = {"PYTHONPATH": str(POC)}
    secret = "gh" + "p_" + "Z9y8X7w6V5" * 3 + "abcdef"
    (repo / "ok.txt").write_text("hello\n")
    git(repo, "add", "ok.txt")
    assert git(repo, "commit", "-qm", "clean", env=env).returncode == 0
    (repo / "leak.py").write_text(f"TOKEN = '{secret}'\n")
    git(repo, "add", "leak.py")
    r = git(repo, "commit", "-qm", "leak", env=env)
    assert r.returncode != 0 and "github-token" in r.stderr and secret not in r.stdout + r.stderr
    (repo / "leak.py").write_text(f"TOKEN = '{secret}'  # pragma: allowlist secret\n")
    git(repo, "add", "leak.py")
    assert git(repo, "commit", "-qm", "allowlisted", env=env).returncode == 0


def test_hook_scans_only_staged_files(repo):
    env = {"PYTHONPATH": str(POC)}
    (repo / "staged.txt").write_text("fine\n")
    (repo / "unstaged_leak.py").write_text("T='" + "gh" + "p_" + "q" * 36 + "'\n")
    git(repo, "add", "staged.txt")
    assert git(repo, "commit", "-qm", "only staged", env=env).returncode == 0


def test_kit_files_are_valid_and_follow_the_pinning_rules():
    hooks = yaml.safe_load((KIT / ".pre-commit-config.yaml").read_text())["repos"]
    assert hooks[0]["rev"].startswith("v8.") and hooks[1]["repo"] == "local"
    cfg = tomllib.loads((KIT / "gitleaks.toml").read_text())
    assert cfg["extend"]["useDefault"] is True and cfg["rules"][0]["id"] == "acme-internal-token"
    wf = yaml.safe_load((KIT / "secrets-scan.yml").read_text())
    text = (KIT / "secrets-scan.yml").read_text()
    assert wf["permissions"] == {"contents": "read"} and "sha256sum -c" in text
    assert wf["jobs"]["gitleaks"]["steps"][0]["with"]["fetch-depth"] == 0


def test_runbook_puts_rotation_before_history_rewrite():
    text = (KIT / "INCIDENT_RUNBOOK.md").read_text()
    assert text.index("rotate") < text.index("filter-repo")


needs_gitleaks = pytest.mark.skipif(
    gitleaks.binary() is None, reason="gitleaks binary not installed"
)


@needs_gitleaks
def test_gitleaks_comparison_is_present_and_sane():
    gl = next(v for k, v in RES["tools"].items() if k.startswith("gitleaks"))
    assert (
        gl["fp_rate"] == 0.0
        and gl["recall"] >= 0.9
        and gl["holdout_recall"] >= KS["holdout_recall"]
    )


@needs_gitleaks
def test_kit_gitleaks_config_detects_the_internal_token_and_honours_the_allowlist(tmp_path):
    (tmp_path / "svc.py").write_text("TOKEN = 'acme_" + "0123456789abcdef" * 2 + "'\n")
    (tmp_path / "doc.md").write_text("Example key AKIAIOSFODNN7" + "EXAMPLE\n")
    found = gitleaks.scan_dir(tmp_path, KIT / "gitleaks.toml")
    assert {Path(f["File"]).name for f in found} == {"svc.py"} and found[0][
        "RuleID"
    ] == "acme-internal-token"
