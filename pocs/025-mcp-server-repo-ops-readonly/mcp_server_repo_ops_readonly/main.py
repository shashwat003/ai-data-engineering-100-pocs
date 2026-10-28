"""POC 025: drive the read-only MCP server over real stdio and record what it returns (and refuses)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table

POC_DIR = Path(__file__).resolve().parent.parent
LIBS = POC_DIR.parent.parent / "libs"
SIBLING = POC_DIR.parent / "024-mcp-server-data-catalog"


def rpc(method: str, params: dict | None = None, id_: int | None = None) -> str:
    m: dict = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if id_ is not None:
        m["id"] = id_
    return json.dumps(m)


def session(requests: list[str], timeout: int = 60) -> list[dict]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(POC_DIR), str(LIBS), str(SIBLING)])}
    p = subprocess.run(
        [sys.executable, "-m", "mcp_server_repo_ops_readonly.server"],
        input="\n".join(requests) + "\n",
        capture_output=True,
        text=True,
        env=env,
        cwd=POC_DIR,
        timeout=timeout,
        check=False,
    )
    return [json.loads(line) for line in p.stdout.splitlines() if line.strip()]


CALLS = [
    ("ci_health_summary", {"days": 7}),
    ("list_workflow_runs", {"conclusion": "failure", "branch": "main", "limit": 3}),
    ("get_run_failure", {"run_id": 1150}),
    ("get_pull_request", {"number": 17}),
    ("list_pull_requests", {"state": "open", "limit": 3}),
    ("search_commits", {"query": "cdc", "limit": 3}),
]
ATTACKS = [
    ("get_run_failure", {"run_id": 1000, "repo": "other/private"}),
    ("create_issue", {"title": "x"}),
    ("list_workflow_runs", {"branch": "main; rm -rf /"}),
    ("list_pull_requests", {"limit": 10_000}),
    ("get_pull_request", {"number": "17 OR 1=1"}),
    ("search_commits", {"query": "x" * 500}),
]


def run() -> dict:
    reqs = [
        rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "demo", "version": "0"},
            },
            1,
        ),
        rpc("notifications/initialized"),
        rpc("tools/list", id_=2),
    ]
    reqs += [
        rpc("tools/call", {"name": n, "arguments": a}, 10 + i) for i, (n, a) in enumerate(CALLS)
    ]
    reqs += [
        rpc("tools/call", {"name": n, "arguments": a}, 100 + i) for i, (n, a) in enumerate(ATTACKS)
    ]
    out = {r["id"]: r for r in session(reqs)}
    tools = out[2]["result"]["tools"]
    calls = []
    for i, (n, a) in enumerate(CALLS):
        r = out[10 + i]["result"]
        calls.append(
            {"tool": n, "args": a, "is_error": r["isError"], "chars": len(r["content"][0]["text"])}
        )
    attacks = []
    for i, (n, a) in enumerate(ATTACKS):
        r = out[100 + i]
        attacks.append(
            {"tool": n, "args": str(a)[:60], "rejected": "error" in r or r["result"]["isError"]}
        )
    texts = " ".join(out[10 + i]["result"]["content"][0]["text"] for i in range(len(CALLS)))
    return {"server": out[1]["result"]["serverInfo"], "tools": [t["name"] for t in tools], "all_read_only": all(t["annotations"]["readOnlyHint"] for t in tools),
            "calls": calls, "attacks": attacks, "token_leaked": "ghp_" in texts, "email_leaked": "oncall@example.com" in texts}  # fmt: skip


def main() -> None:
    res = run()
    write_json(POC_DIR / "results" / "results.json", res)
    md = (
        "# Results: read-only MCP server over stdio against a MOCK repository\n\n"
        f"Server `{res['server']['name']}` exposes {len(res['tools'])} tools ({', '.join(res['tools'])}); all annotated read-only: {res['all_read_only']}. "
        "Data comes from a synthetic in-memory repository; no GitHub API was called.\n\n"
        + markdown_table(
            ["tool", "arguments", "error", "reply chars"],
            [(c["tool"], str(c["args"]), c["is_error"], c["chars"]) for c in res["calls"]],
        )
        + "\n\nRefused or rejected requests:\n\n"
        + markdown_table(
            ["tool", "arguments", "rejected"],
            [(a["tool"], a["args"], a["rejected"]) for a in res["attacks"]],
        )
        + f"\n\nFake token leaked into any reply: {res['token_leaked']}; email leaked: {res['email_leaked']}.\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
