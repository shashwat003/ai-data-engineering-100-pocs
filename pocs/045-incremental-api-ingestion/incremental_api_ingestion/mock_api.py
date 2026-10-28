"""A deterministic local HTTP API with the failure modes real SaaS APIs have.

Records change over time (`updated_at` is a logical clock). The server paginates with opaque
cursors, answers 429 with Retry-After, fails some requests with 500, overlaps pages, requires a bearer
token and can add a field mid-way (schema drift). Nothing leaves localhost.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = "demo-token"  # synthetic, only valid against this mock


@dataclass
class ApiState:
    records: list[dict]  # each: id, updated_at (int), amount, status, deleted
    rate_limit_every: int = 0  # every Nth request gets 429
    error_every: int = 0  # every Nth request gets 500
    overlap: int = (
        0  # repeat the last `overlap` records of the previous page at the start of the next
    )
    drift_after_request: int | None = None  # start returning an extra field
    requests: int = 0
    log: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


def make_records(n: int, t_end: int) -> list[dict]:
    return [{"id": i, "updated_at": 1 + (i * 7919) % t_end, "amount": round(10 + (i % 97) * 1.5, 2),
             "status": ["new", "paid", "shipped"][i % 3], "deleted": False} for i in range(1, n + 1)]  # fmt: skip


def make_handler(state: ApiState):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def _send(self, code: int, body: dict, headers: dict | None = None):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            with state.lock:
                state.requests += 1
                n = state.requests
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self._send(401, {"error": "unauthorized"})
            if state.rate_limit_every and n % state.rate_limit_every == 0:
                state.log.append("429")
                return self._send(429, {"error": "slow down"}, {"Retry-After": "2"})
            if state.error_every and n % state.error_every == 0:
                state.log.append("500")
                return self._send(500, {"error": "boom"})
            q = parse_qs(urlparse(self.path).query)
            since = int(q.get("updated_since", ["0"])[0])
            limit = min(int(q.get("limit", ["50"])[0]), 100)
            offset = int(q.get("cursor", ["0"])[0])
            rows = sorted(
                (r for r in state.records if r["updated_at"] > since),
                key=lambda r: (r["updated_at"], r["id"]),
            )
            start = max(0, offset - state.overlap)
            page = rows[start : offset + limit]
            nxt = str(offset + limit) if offset + limit < len(rows) else None
            if state.drift_after_request is not None and n > state.drift_after_request:
                page = [{**r, "currency": "EUR"} for r in page]
            self._send(200, {"data": page, "next_cursor": nxt})

    return H


class MockApi:
    def __init__(self, state: ApiState):
        self.state = state
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
