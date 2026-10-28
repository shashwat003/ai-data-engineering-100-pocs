"""Tiny HTTP front end (stdlib) so approvals can arrive from a chat bot or web page."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .workflow import Workflow, WorkflowError


def serve(wf: Workflow) -> tuple[ThreadingHTTPServer, str]:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length", 0))
            if n > 10_000:
                raise WorkflowError("body too large")
            return json.loads(self.rfile.read(n) or b"{}")

        def do_POST(self):
            try:
                body = self._body()
                if self.path == "/requests":
                    return self._send(
                        201,
                        {
                            "id": wf.submit(
                                body["action"], body.get("payload", {}), body["requester"]
                            )
                        },
                    )
                if self.path == "/decisions":
                    return self._send(200, {"state": wf.decide(body["token"], body["verdict"])})
                self._send(404, {"error": "not found"})
            except WorkflowError as e:
                self._send(409, {"error": str(e)})
            except (KeyError, json.JSONDecodeError):
                self._send(400, {"error": "bad request"})

        def do_GET(self):
            try:
                if self.path.startswith("/requests/"):
                    return self._send(200, wf.status(int(self.path.rsplit("/", 1)[1])))
                self._send(404, {"error": "not found"})
            except (WorkflowError, ValueError) as e:
                self._send(404, {"error": str(e)})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
