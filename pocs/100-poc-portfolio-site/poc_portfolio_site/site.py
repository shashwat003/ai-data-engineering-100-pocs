"""Static, dependency-light portfolio site: searchable index + one page per POC. No external requests by default."""

from __future__ import annotations

import html
import json
from pathlib import Path

from markdown_it import MarkdownIt

from .model import MATURITY, Poc

MD = MarkdownIt("commonmark", {"html": False}).enable(
    "table"
)  # raw HTML in READMEs is escaped, never passed through

CSS = """:root{--bg:#fff;--fg:#1b1f24;--mut:#59636e;--line:#d0d7de;--acc:#0969da;--ok:#1a7f37;--warn:#9a6700}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--mut:#9198a1;--line:#30363d;--acc:#4493f8;--ok:#3fb950;--warn:#d29922}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}main{max-width:1100px;margin:0 auto;padding:16px}
input,select{font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
table{border-collapse:collapse;width:100%;margin:12px 0}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
.b{display:inline-block;padding:0 8px;border-radius:12px;border:1px solid var(--line);font-size:.8em;margin-right:4px}.verified{color:var(--ok)}.tested{color:var(--acc)}.planned,.scaffold{color:var(--mut)}.flag{color:var(--warn)}
a{color:var(--acc)}pre{overflow:auto;padding:8px;border:1px solid var(--line);border-radius:6px}"""


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def badges(p: Poc) -> str:
    out = [f'<span class="b {MATURITY[p.maturity]}">{MATURITY[p.maturity]}</span>']
    for lab in sorted(p.labels - {"tested"}):
        out.append(f'<span class="b flag">{esc(lab)}</span>')
    for m in sorted(p.markers):
        out.append(f'<span class="b">{esc(m.lstrip(".").replace("-", " "))}</span>')
    return "".join(out)


def page(title: str, body: str, root_rel: str = "") -> str:
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{CSS}</style></head><body><main><p><a href="{root_rel}index.html">&larr; Portfolio</a></p>{body}</main></body></html>'


def render_readme(p: Poc) -> str:
    html_body = MD.render(p.readme or "_No README._")
    # Mermaid blocks stay readable as code (no script is loaded); the source is escaped by markdown-it.
    return html_body


def build_site(pocs: list[Poc], out_dir: str | Path, *, flagships: set[int] | None = None) -> dict:
    out = Path(out_dir)
    (out / "poc").mkdir(parents=True, exist_ok=True)
    flagships = flagships or set()
    index = []
    for p in pocs:
        index.append(
            {
                "id": p.id,
                "slug": p.slug,
                "title": p.title,
                "category": p.category,
                "stack": p.stack,
                "maturity": MATURITY[p.maturity],
                "labels": sorted(p.labels - {"tested"}),
                "flagship": p.id in flagships,
                "built": p.built,
            }
        )
        if p.built:
            body = f"<h1>POC {p.id:03d}: {esc(p.title)}</h1><p>{badges(p)}</p>{render_readme(p)}"
            (out / "poc" / f"{p.id:03d}.html").write_text(
                page(f"POC {p.id:03d}", body, "../"), encoding="utf-8"
            )
    data = json.dumps(index).replace("</", "<\\/")  # safe inside <script>
    cats = sorted({p.category for p in pocs})
    built = sum(p.built for p in pocs)
    by = {m: sum(MATURITY[p.maturity] == m for p in pocs) for m in MATURITY.values()}
    script = """const D=JSON.parse(document.getElementById('d').textContent);const q=document.getElementById('q'),c=document.getElementById('c'),m=document.getElementById('m'),t=document.getElementById('t');
function esc(s){return s.replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]))}
function draw(){const s=q.value.toLowerCase();t.innerHTML=D.filter(p=>(!c.value||p.category===c.value)&&(!m.value||p.maturity===m.value)&&(!s||(p.title+' '+p.stack+' '+p.slug).toLowerCase().includes(s))).map(p=>
`<tr><td>${p.id}</td><td>${p.built?`<a href="poc/${String(p.id).padStart(3,'0')}.html">${esc(p.title)}</a>`:esc(p.title)}${p.flagship?' &#9733;':''}</td><td>${esc(p.category)}</td><td>${esc(p.stack)}</td><td><span class="b ${p.maturity}">${p.maturity}</span>${p.labels.map(l=>`<span class="b flag">${esc(l)}</span>`).join('')}</td></tr>`).join('')}
[q,c,m].forEach(e=>e.addEventListener('input',draw));draw();"""
    body = (
        f"<h1>100 real-world AI &amp; data engineering POCs</h1><p>{built} of {len(pocs)} built. "
        + ", ".join(f"{n} {k}" for k, n in by.items())
        + ".</p>"
        '<p><input id="q" placeholder="Search title, stack…" aria-label="Search"> <select id="c" aria-label="Category"><option value="">All categories</option>'
        + "".join(f"<option>{esc(c)}</option>" for c in cats)
        + '</select> <select id="m" aria-label="Maturity"><option value="">All maturity</option>'
        + "".join(f"<option>{v}</option>" for v in MATURITY.values())
        + '</select></p><table><thead><tr><th>#</th><th>POC</th><th>Category</th><th>Stack</th><th>Status</th></tr></thead><tbody id="t"></tbody></table>'
        f'<script id="d" type="application/json">{data}</script><script>{script}</script>'
    )
    (out / "index.html").write_text(page("POC portfolio", body), encoding="utf-8")
    (out / "search-index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    return {"pages": built + 1, "built": built, "total": len(pocs), "by_maturity": by}
