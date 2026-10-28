"""Direct abuse of the MCP catalogue server (agent bypassed): does the server defend itself?"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from incident_triage_agent.main import CATALOGUE_META
from mcp_server_data_catalog.catalogue import Catalogue
from mcp_server_data_catalog.server import CatalogServer


@dataclass(frozen=True)
class Abuse:
    id: str
    message: object
    initialise: bool = True
    expect: str = "refuse"  # refuse = error/isError; redact = data returned without PII


def call(name: str, args: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }


ABUSES = [
    Abuse(
        "sql_injection_in_dataset",
        call("get_dataset_schema", {"dataset": "orders'; DROP TABLE orders;--"}),
    ),
    Abuse("path_traversal_in_dataset", call("get_dataset_schema", {"dataset": "../../etc/passwd"})),
    Abuse(
        "sample_pii_rows",
        call("sample_rows", {"dataset": "customers", "rows": 10}),
        expect="redact",
    ),
    Abuse(
        "profile_pii_column", call("profile_column", {"dataset": "customers", "column": "email"})
    ),
    Abuse(
        "profile_injected_column",
        call("profile_column", {"dataset": "orders", "column": "1; select 1"}),
    ),
    Abuse("oversized_query", call("search_datasets", {"query": "a" * 10_000})),
    Abuse("huge_limit", call("search_datasets", {"query": "orders", "limit": 10_000})),
    Abuse("huge_row_count", call("sample_rows", {"dataset": "orders", "rows": 1000})),
    Abuse("unknown_tool_execute_sql", call("execute_sql", {"sql": "select * from customers"})),
    Abuse(
        "extra_argument_sql", call("get_dataset_schema", {"dataset": "orders", "sql": "select 1"})
    ),
    Abuse("call_before_initialize", call("list_datasets", {}), initialise=False),
    Abuse("not_jsonrpc", ["tools/call"]),
    Abuse("wrong_type_arguments", call("get_lineage", {"dataset": ["orders"]})),
]


def run_abuse(db: Path) -> list[dict]:
    rows = []
    for a in ABUSES:
        server = CatalogServer(Catalogue(db, CATALOGUE_META))
        if a.initialise:
            server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        reply = server.handle(a.message)
        text = json.dumps(reply)
        refused = reply is not None and (
            "error" in reply or reply.get("result", {}).get("isError") is True
        )
        leaked_pii = "@" in text and "example.com" in text
        blocked = refused if a.expect == "refuse" else not leaked_pii
        rows.append({"id": a.id, "expect": a.expect, "refused": refused,
                     "pii_in_response": leaked_pii, "blocked": blocked})  # fmt: skip
    return rows
