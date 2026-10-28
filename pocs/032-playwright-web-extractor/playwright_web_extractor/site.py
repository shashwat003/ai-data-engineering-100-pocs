"""A local test website that misbehaves like real ones: client-side rendering, flaky API, cookie banner, markup drift.

Served from 127.0.0.1 only; the extractor never touches the public internet. Ground truth is exported
so the extractor can be scored exactly.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PER_PAGE, PAGES = 10, 4


def catalogue() -> list[dict]:
    items = [
        {"id": f"P{i:03d}", "name": f"Widget {i}", "price": f"{10 + i * 1.5:.2f}"}
        for i in range(1, PER_PAGE * PAGES + 1)
    ]
    items[4]["price"] = "N/A"  # unparsable price
    items[12]["name"] = ""  # missing name
    items[21]["price"] = "-3.00"  # negative price
    items[30]["id"] = items[29]["id"]  # duplicate id on a later page
    return items


def valid_ids() -> set[str]:
    """Records a correct extractor should keep (valid, first occurrence)."""
    seen, ok = set(), set()
    for r in catalogue():
        try:
            good = bool(r["name"]) and float(r["price"]) >= 0 and r["id"] not in seen
        except ValueError:
            good = False
        seen.add(r["id"])
        if good:
            ok.add(r["id"])
    return ok


SHELL = """<!doctype html><html><head><meta charset="utf-8"><title>Shop</title></head><body>
{banner}
<div id="app"><p class="loading">Loading…</p></div>
<script>
const page = {page}, layout = "{layout}";
function render(items) {{
  const cls = layout === "v1" ? ["product","title","price"] : ["item-card","item-name","item-price"];
  document.getElementById("app").innerHTML = items.map(p =>
    `<div class="${{cls[0]}}" data-id="${{p.id}}"><span class="${{cls[1]}}">${{p.name}}</span><span class="${{cls[2]}}">${{p.price}}</span></div>`).join("")
    + ({pages} > page ? `<a id="next" href="/products?page=${{page+1}}">Next</a>` : "");
}}
function load() {{
  document.getElementById("app").innerHTML = '<p class="loading">Loading…</p>';
  setTimeout(() => fetch("/api/products?page=" + page).then(r => {{
    if (!r.ok) throw new Error("http " + r.status);
    return r.json();
  }}).then(render).catch(() => {{
    document.getElementById("app").innerHTML = '<div class="error">Could not load. <button id="retry">Retry</button></div>';
    document.getElementById("retry").onclick = load;
  }}), 120);
}}
load();
</script></body></html>"""
BANNER = '<div id="cookie" style="position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9"><button id="accept" onclick="document.getElementById(\'cookie\').remove()">Accept</button></div>'


class Site:
    def __init__(
        self, *, flaky_pages=(2, 4), drift_from_page=3, always_fail=(), disallow_products=False
    ):
        self.flaky_pages, self.drift_from = set(flaky_pages), drift_from_page
        self.always_fail, self.disallow = set(always_fail), disallow_products
        self.hits: dict[str, int] = {}
        self.lock = threading.Lock()
        site = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="text/html"):
                data = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype + "; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                u = urlparse(self.path)
                q = parse_qs(u.query)
                page = int(q.get("page", ["1"])[0]) if q.get("page", ["1"])[0].isdigit() else 1
                with site.lock:
                    site.hits[u.path] = site.hits.get(u.path, 0) + 1
                if u.path == "/robots.txt":
                    return self._send(
                        200,
                        "User-agent: *\nDisallow: /"
                        + ("products" if site.disallow else "admin")
                        + "\n",
                        "text/plain",
                    )
                if u.path == "/admin":
                    return self._send(403, "no")
                if u.path == "/products":
                    layout = "v1" if page < site.drift_from else "v2"
                    return self._send(
                        200, SHELL.format(banner=BANNER, page=page, pages=PAGES, layout=layout)
                    )
                if u.path == "/api/products":
                    key = f"api{page}"
                    with site.lock:
                        site.hits[key] = site.hits.get(key, 0) + 1
                        first = site.hits[key] == 1
                    if page in site.always_fail or (page in site.flaky_pages and first):
                        return self._send(503, json.dumps({"error": "busy"}), "application/json")
                    items = catalogue()[(page - 1) * PER_PAGE : page * PER_PAGE]
                    return self._send(200, json.dumps(items), "application/json")
                return self._send(404, "not found")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()
