import asyncio
import json
import sys

import pytest
from mcp_server_repo_ops_readonly.data import FAKE_TOKEN, INJECTION, build
from mcp_server_repo_ops_readonly.main import POC_DIR, SIBLING, rpc, run, session
from mcp_server_repo_ops_readonly.reader import HttpGitHubReader, MockGitHub
from mcp_server_repo_ops_readonly.server import RepoOpsServer
from mcp_server_repo_ops_readonly.tools import RepoOps, RepoOpsError, clean

R = run()
SRV = RepoOpsServer(MockGitHub())


def call(name, args=None, srv=SRV):
    srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    return srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        }
    )


def test_demo_session_over_real_stdio_succeeds_and_attacks_are_rejected():
    assert R["server"]["name"] == "repo-ops-readonly" and len(R["tools"]) == 6
    assert all(not c["is_error"] for c in R["calls"])
    assert all(a["rejected"] for a in R["attacks"])


def test_every_tool_is_annotated_read_only_and_no_write_tool_exists():
    assert R["all_read_only"]
    assert not any(
        w in n
        for n in R["tools"]
        for w in ("create", "delete", "update", "merge", "push", "comment", "close")
    )


def test_secrets_and_emails_never_reach_replies():
    assert not R["token_leaked"] and not R["email_leaked"]
    full = json.dumps(call("get_run_failure", {"run_id": 1150, "tail": 50}))
    assert FAKE_TOKEN not in full and "<redacted>" in full and "oncall@example.com" not in full


def test_untrusted_text_is_flagged_and_injection_is_returned_only_as_data():
    r = call("get_pull_request", {"number": 17})["result"]
    assert not r["isError"] and "untrusted" in r["structuredContent"]["_note"]
    assert (
        INJECTION.split(":")[0] in r["content"][0]["text"]
    )  # present, but inside a data field, not obeyed


def test_extra_and_cross_repo_arguments_are_rejected():
    r = call("get_run_failure", {"run_id": 1150, "repo": "other/private"})
    assert r["error"]["code"] == -32602 and "unexpected" in r["error"]["message"]


@pytest.mark.parametrize(
    "name,args",
    [
        ("list_pull_requests", {"limit": 21}),
        ("list_pull_requests", {"state": "deleted"}),
        ("get_pull_request", {"number": 0}),
        ("get_pull_request", {"number": "1"}),
        ("get_pull_request", {"number": True}),
        ("list_workflow_runs", {"limit": 0}),
        ("search_commits", {"query": ""}),
        ("ci_health_summary", {"days": 31}),
        ("get_run_failure", {"run_id": 1.5}),
        ("nope", {}),
    ],
)
def test_invalid_arguments_are_protocol_errors(name, args):
    assert "error" in call(name, args)


def test_invalid_branch_name_is_refused_not_executed():
    r = call("list_workflow_runs", {"branch": "main; rm -rf /"})["result"]
    assert r["isError"] and "invalid branch" in r["content"][0]["text"]


def test_not_found_and_success_run_give_safe_errors():
    assert call("get_pull_request", {"number": 9999})["result"]["isError"]
    ok_run = next(r["id"] for r in build()["runs"] if r["conclusion"] == "success")
    r = call("get_run_failure", {"run_id": ok_run})["result"]
    assert r["isError"] and "succeeded" in r["content"][0]["text"]


def test_tools_require_initialize():
    s = RepoOpsServer(MockGitHub())
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r["error"]["code"] == -32002


def test_ci_health_summary_matches_the_data():
    h = RepoOps(MockGitHub()).ci_health_summary(30)["items"]
    runs = build()["runs"]
    assert h["runs"] == len(runs) and h["failure_rate"] == round(
        sum(r["conclusion"] == "failure" for r in runs) / len(runs), 3
    )
    assert "test_backfill_window" in h["suspected_flaky_on_main"]
    with pytest.raises(RepoOpsError):
        RepoOps(MockGitHub()).ci_health_summary(0)


def test_pull_request_listing_is_sorted_and_filtered():
    items = RepoOps(MockGitHub()).list_pull_requests("merged", 20)["items"]
    assert all(p["state"] == "merged" for p in items)
    assert [p["created_at"] for p in items] == sorted(
        (p["created_at"] for p in items), reverse=True
    )


def test_clean_redacts_and_truncates():
    assert (
        "<redacted>" in clean("password=hunter2")
        and "<email>" in clean("a@b.example")
        and len(clean("x" * 999)) == 300
    )


def test_server_info_is_not_the_catalogue_servers():
    r = SRV.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})["result"]
    assert r["serverInfo"]["name"] == "repo-ops-readonly" and "untrusted" in r["instructions"]


# ---- the HTTP adapter (UNVERIFIED against real GitHub): only check that it is GET-only and path-restricted


class _FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"[]"


def test_http_adapter_uses_get_only_and_allow_listed_paths():
    seen = []

    def opener(req, timeout=0):
        seen.append((req.get_method(), req.full_url, req.get_header("Authorization")))
        return _FakeResp()

    r = HttpGitHubReader("acme/data-platform", token="t0k", opener=opener)
    r.prs(), r.runs(), r.commits()
    assert {m for m, _, _ in seen} == {"GET"} and all(
        "/repos/acme/data-platform/" in u for _, u, _ in seen
    )
    with pytest.raises(PermissionError):
        r._get("issues")
    with pytest.raises(PermissionError):
        r._get("../../user")


@pytest.mark.parametrize("bad", ["", "noslash", "a/b/c", "a b/c", "a/b?x=1"])
def test_http_adapter_rejects_odd_repo_names(bad):
    with pytest.raises(ValueError):
        HttpGitHubReader(bad)


def test_session_helper_handles_bad_lines():
    out = session(["not json", rpc("ping", id_=7)])
    assert out[0]["error"]["code"] == -32700 and out[1]["result"] == {}


def test_official_mcp_client_can_use_the_server():
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server_repo_ops_readonly.server"],
        env={"PYTHONPATH": f"{POC_DIR}:{POC_DIR.parent.parent / 'libs'}:{SIBLING}"},
        cwd=str(POC_DIR),
    )

    async def scenario():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
            await s.initialize()
            tools = await s.list_tools()
            ok = await s.call_tool("ci_health_summary", {"days": 7})
            bad = await s.call_tool("list_workflow_runs", {"branch": "a b"})
            return tools, ok, bad

    tools, ok, bad = asyncio.run(scenario())
    assert len(tools.tools) == 6 and ok.is_error is False and bad.is_error is True
