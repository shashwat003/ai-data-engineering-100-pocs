"""Local HTTP endpoint speaking the OpenLineage ingest contract (POST /api/v1/lineage). Binds to 127.0.0.1 only."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .store import InvalidEvent, LineageStore

MAX_BODY = 1_000_000


def make_server(store: LineageStore, port: int = 0) -> ThreadingHTTPServer:
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def _reply(self, code: int, body: dict | None = None):
            data = json.dumps(body or {}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path != "/api/v1/lineage":
                return self._reply(404, {"error": "not found"})
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                return self._reply(413 if n > MAX_BODY else 400, {"error": "bad body size"})
            try:
                ev = json.loads(self.rfile.read(n))
                with lock:
                    store.ingest(ev)
                return self._reply(201)
            except (json.JSONDecodeError, InvalidEvent) as e:
                with lock:
                    store.rejected += 1
                return self._reply(400, {"error": str(e)})

    return ThreadingHTTPServer(("127.0.0.1", port), H)
