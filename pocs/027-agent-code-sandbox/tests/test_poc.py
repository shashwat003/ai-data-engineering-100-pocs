import os
import tempfile
import time
from pathlib import Path

import pytest
from agent_code_sandbox.attacks import ATTACKS, CANARY, LEGIT, Lab, render, succeeded
from agent_code_sandbox.policy import PolicyViolation, check
from agent_code_sandbox.sandbox import Layers, netns_available, run

FULL = Layers(network_isolation=True)


def attempt(attack, layers, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        lab = Lab(Path(tmp))
        try:
            r = run(render(attack, lab), cpu_s=1, mem_mb=200, wall_s=2.5, layers=layers, **kw)
            time.sleep(0.05)
            return succeeded(attack, lab, r), r
        finally:
            lab.close()


@pytest.mark.parametrize(
    "code",
    [
        "import os",
        "import subprocess as s",
        "from socket import socket",
        "import os.path",
        "x = ().__class__",
        "open('f')",
        "eval('1')",
        "__import__('os')",
        "getattr(1, 'x')",
        "def f(:",
        "from . import x",
    ],
)
def test_policy_rejects(code):
    with pytest.raises(PolicyViolation):
        check(code)


@pytest.mark.parametrize(
    "code",
    [
        "import math\nprint(math.sqrt(2))",
        "from collections import Counter",
        "import json, re, statistics",
        "x = [i * i for i in range(10)]",
    ],
)
def test_policy_allows_analysis_code(code):
    check(code)


def test_policy_rejects_oversized_code():
    with pytest.raises(PolicyViolation, match="too long"):
        check("x = 1\n" * 10_000)


@pytest.mark.parametrize("a", ATTACKS, ids=lambda a: a.name)
def test_no_attack_succeeds_with_all_layers_on(a):
    won, r = attempt(a, FULL)
    assert not won, (a.name, r)
    if a.category == "dos":
        assert r.status in ("timeout", "killed", "error", "output_truncated") and r.seconds < 6


def test_legitimate_code_runs_correctly():
    for name, code, want in LEGIT:
        r = run(code)
        assert r.status == "ok" and r.stdout.strip() == want, name


def test_attacks_are_real_each_layer_removal_lets_something_through():
    """Control group: the oracles must be able to fire, otherwise 'blocked' proves nothing."""
    by = {a.name: a for a in ATTACKS}
    assert attempt(by["subclasses_walk"], Layers(static_policy=False))[
        0
    ]  # builtins table does not stop dunder walks
    no_runtime = Layers(static_policy=False, restricted_builtins=False, rlimits=False)
    for name in (
        "open_secret_file",
        "os_system_touch",
        "subprocess_touch",
        "dunder_import",
        "eval_string",
        "write_marker_open",
    ):
        assert attempt(by[name], no_runtime)[0], name


def test_static_layer_alone_stops_exec_before_any_process_starts():
    a = next(x for x in ATTACKS if x.name == "os_system_touch")
    _, r = attempt(a, Layers(restricted_builtins=False, rlimits=False))
    assert r.status == "rejected" and r.stopped_by == "static_policy" and r.seconds == 0.0


def test_restricted_builtins_alone_block_imports_and_open_at_runtime():
    for name in ("open_secret_file", "import_os_environ", "socket_connect"):
        a = next(x for x in ATTACKS if x.name == name)
        won, r = attempt(a, Layers(static_policy=False, network_isolation=False))
        assert not won and r.status == "error", (name, r)


def test_infinite_loop_is_ended_by_cpu_limit_or_wall_clock():
    t = time.perf_counter()
    r = run("while True:\n    pass", cpu_s=1, wall_s=3)
    assert r.status in ("killed", "timeout") and time.perf_counter() - t < 4
    r2 = run("while True:\n    pass", cpu_s=100, wall_s=1, layers=Layers(rlimits=False))
    assert r2.status == "timeout" and r2.stopped_by == "wall_clock"


def test_memory_limit_stops_allocation():
    r = run("x = bytearray(2 * 10**9)", mem_mb=100)
    assert r.status == "error" and "MemoryError" in r.stderr


def test_output_is_capped():
    r = run("print('A' * 10**7)", out_bytes=1000)
    assert r.status == "output_truncated" and len(r.stdout) == 1000


def test_environment_is_scrubbed_and_tracebacks_do_not_leak_paths():
    import os

    os.environ["SANDBOX_TEST_SECRET"] = CANARY
    try:
        r = run("import math\nraise ValueError('boom')")
        assert CANARY not in r.stdout + r.stderr and r.status == "error"
        assert "/home" not in r.stderr and "Traceback" not in r.stderr
    finally:
        del os.environ["SANDBOX_TEST_SECRET"]


@pytest.mark.skipif(
    not netns_available(),
    reason="network namespaces need privileges not available here (reported, not assumed)",
)
def test_network_namespace_blocks_loopback_even_without_other_layers():
    a = next(x for x in ATTACKS if x.name == "socket_connect")
    won, _ = attempt(
        a, Layers(static_policy=False, restricted_builtins=False, network_isolation=True)
    )
    assert not won
    won_off, _ = attempt(
        a, Layers(static_policy=False, restricted_builtins=False, network_isolation=False)
    )
    assert won_off


@pytest.mark.skipif(
    os.geteuid() == 0,
    reason="RLIMIT_NPROC is not enforced for root; verified on unprivileged CI runners",
)
def test_nproc_rlimit_blocks_process_creation_for_unprivileged_users():
    a = next(x for x in ATTACKS if x.name == "os_system_touch")
    won, _ = attempt(a, Layers(static_policy=False, restricted_builtins=False, rlimits=True))
    assert not won  # fork fails under RLIMIT_NPROC=0 even with the other two layers off
