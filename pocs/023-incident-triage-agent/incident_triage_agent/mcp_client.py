"""Minimal MCP client (JSON-RPC 2.0) for the POC 024 catalogue server.

Two transports: in-process (every message is still serialised to JSON and back) and stdio (a real
subprocess). The client treats everything the server returns as untrusted data.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Protocol

PROTOCOL_VERSION = "2025-06-18"


class McpError(RuntimeError):
    pass


class Transport(Protocol):
    def send(self, message: dict) -> dict | None: ...
    def close(self) -> None: ...


class InProcessTransport:
    def __init__(self, server: Any):
        self.server = server

    def send(self, message: dict) -> dict | None:
        reply = self.server.handle(json.loads(json.dumps(message)))
        return json.loads(json.dumps(reply)) if reply is not None else None

    def close(self) -> None:
        pass


class StdioTransport:
    def __init__(self, argv: list[str], cwd: Path, pythonpath: str):
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, cwd=cwd, env={"PYTHONPATH": pythonpath, "PATH": ""},
        )  # fmt: skip

    def send(self, message: dict) -> dict | None:
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        if "id" not in message:
            return None
        line = self.proc.stdout.readline()
        if not line:
            raise McpError("server closed the connection")
        return json.loads(line)

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.wait(timeout=5)


class McpClient:
    def __init__(self, transport: Transport):
        self.t, self._id = transport, 0
        self._request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                     "clientInfo": {"name": "incident-triage-agent", "version": "1"}})  # fmt: skip
        self.t.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        reply = self.t.send(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        )
        if reply is None or "error" in reply:
            raise McpError((reply or {}).get("error", {}).get("message", "no reply"))
        return reply["result"]

    def list_tools(self) -> list[dict]:
        return self._request("tools/list")["tools"]

    def call(self, name: str, arguments: dict) -> dict:
        """Return the tool's structured result, or raise McpError if it reported an error."""
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise McpError(" ".join(c.get("text", "") for c in result.get("content", [])))
        return result.get("structuredContent", {})

    def close(self) -> None:
        self.t.close()
