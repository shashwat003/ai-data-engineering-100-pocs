"""A fake Ollama HTTP server (stdlib) so the harness and OllamaClient are exercised over real HTTP.

Each fake model has an accuracy, a latency and a memory footprint. Answers come from a gold table,
so these numbers describe the harness, NOT any real model.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROFILES = {
    "fake-tiny:1b": {"acc": 0.55, "ms": 3, "mb": 900.0, "tps": 120},
    "fake-small:3b": {"acc": 0.78, "ms": 7, "mb": 2300.0, "tps": 70},
    "fake-mid:7b": {"acc": 0.93, "ms": 16, "mb": 5200.0, "tps": 35},
    "fake-wasteful:7b": {"acc": 0.70, "ms": 20, "mb": 6100.0, "tps": 30},  # dominated by fake-mid
}


def _u(*parts) -> float:
    return (
        int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "big")
        / 2**64
    )


def make_server(
    gold: dict[str, str], profiles=PROFILES, fail_prefix: str | None = None
) -> ThreadingHTTPServer:
    loaded: set[str] = set()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def _send(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path == "/api/tags":
                self._send(
                    {
                        "models": [
                            {"name": n, "size": int(p["mb"] * 1e6)} for n, p in profiles.items()
                        ]
                    }
                )
            elif self.path == "/api/ps":
                self._send(
                    {"models": [{"name": n, "size": int(profiles[n]["mb"] * 1e6)} for n in loaded]}
                )
            elif self.path == "/api/version":
                self._send({"version": "fake-0"})
            else:
                self._send({"error": "not found"}, 404)

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            if self.path != "/api/generate":
                return self._send({"error": "not found"}, 404)
            model, prompt = req.get("model"), req.get("prompt", "")
            if model not in profiles:
                return self._send({"error": "model not found"}, 404)
            if fail_prefix and prompt.startswith(fail_prefix):
                return self._send({"error": "boom"}, 500)
            p = profiles[model]
            loaded.add(model)
            time.sleep(p["ms"] / 1000)
            right = gold.get(prompt)
            if right is None:
                text = "OK"
            elif (
                _u(model, prompt, req.get("options", {}).get("seed", 0) // 99) < p["acc"]
            ):  # stable across seeds
                text = right
            else:
                text = "I am not sure."
            toks = max(1, len(text.split()))
            self._send(
                {
                    "response": text,
                    "prompt_eval_count": max(1, len(prompt) // 4),
                    "eval_count": toks,
                    "done": True,
                }
            )

    return ThreadingHTTPServer(("127.0.0.1", 0), H)


def serve(gold, **kw):
    s = make_server(gold, **kw)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, f"http://127.0.0.1:{s.server_port}"
