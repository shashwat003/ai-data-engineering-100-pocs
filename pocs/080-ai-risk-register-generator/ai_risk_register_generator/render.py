from __future__ import annotations

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

TEMPLATE = """# AI risk register (draft for human review)

> Screening aid based on a simplified reading of the EU AI Act. Not legal advice.

Systems: {{ summary.systems }} | prohibited: {{ summary.by_tier.prohibited }} | high: {{ summary.by_tier.high }} | limited: {{ summary.by_tier.limited }} | minimal: {{ summary.by_tier.minimal }} | open gaps: {{ summary.open_gaps }}

{% for i in items|sort(attribute='tier_rank') %}
## {{ i.id }} {{ i.name }} - {{ i.tier|upper }}
- Purpose: {{ i.purpose }}
- Why: {{ i.reasons|join('; ') }}
- Action: {{ i.action }}
{% if i.obligations %}
| Obligation | Status |
|---|---|
{% for k, o in i.obligations.items() %}| {{ o.label }} | {{ o.status }} |
{% endfor %}{% endif %}
{% endfor %}
"""
RANK = {"prohibited": 0, "high": 1, "limited": 2, "minimal": 3}


def _esc(v):
    """Neutralise markdown table/heading breakers coming from inventory text."""
    return str(v).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render(items: list[dict], summary: dict) -> str:
    env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, trim_blocks=True)
    clean = []
    for i in items:
        c = dict(i)
        for k in ("name", "purpose", "action"):
            c[k] = _esc(c[k])
        c["reasons"] = [_esc(r) for r in c["reasons"]]
        c["obligations"] = {
            k: {"label": _esc(o["label"]), "status": o["status"]}
            for k, o in c["obligations"].items()
        }
        c["tier_rank"] = RANK[c["tier"]]
        clean.append(c)
    return env.from_string(TEMPLATE).render(items=clean, summary=summary)
