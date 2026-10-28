"""Generate a model card (Markdown) from an MLflow model version, evaluation slices and curated context.

Facts (parameters, metrics, version, lineage) come from the registry and from computed evaluation;
judgements (intended use, limitations) come from a reviewed YAML file. The generator refuses to emit
a card with required sections missing, and renders user-supplied text as data, never as a template.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from sklearn.metrics import roc_auc_score

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

REQUIRED_CONTEXT = ("intended_use", "out_of_scope", "training_data", "limitations", "contact")

TEMPLATE = """\
# Model card: {{ name }} v{{ version }}

*Generated {{ generated }} from MLflow run `{{ run_id }}`. Do not edit by hand; edit the context file and regenerate.*

## Overview
{{ summary }}

## Intended use
{{ ctx.intended_use }}

### Out of scope
{% for item in ctx.out_of_scope %}- {{ item }}
{% endfor %}
## Training data
{{ ctx.training_data }}

## Training procedure
| parameter | value |
|---|---|
{% for k, v in params.items() %}| {{ k }} | {{ v }} |
{% endfor %}
## Evaluation
Held-out set of {{ n_eval }} examples.

| metric | value | 95% interval |
|---|---|---|
{% for m in metrics %}| {{ m.name }} | {{ "%.3f"|format(m.value) }} | {{ "%.3f"|format(m.lo) }} to {{ "%.3f"|format(m.hi) }} |
{% endfor %}
### Performance by subgroup
| subgroup | n | AUC |
|---|---|---|
{% for s in slices %}| {{ s.name }} | {{ s.n }} | {{ "%.3f"|format(s.auc) }} |
{% endfor %}
Largest subgroup gap: **{{ "%.3f"|format(gap) }}** AUC{% if gap > gap_threshold %} (above the {{ gap_threshold }} review threshold){% endif %}.

## Limitations and risks
{% for item in ctx.limitations %}- {{ item }}
{% endfor %}
{% if changes %}## Changes since v{{ prev_version }}
{% for c in changes %}- {{ c }}
{% endfor %}{% endif %}
## Contact
{{ ctx.contact }}
"""

ENV = SandboxedEnvironment(
    undefined=StrictUndefined, autoescape=False, trim_blocks=False, keep_trailing_newline=True
)


class CardError(ValueError):
    pass


@dataclass
class Metric:
    name: str
    value: float
    lo: float
    hi: float


def bootstrap_auc(y, p, n=300, seed=0) -> Metric:
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if len(set(y[i])) == 2:
            vals.append(roc_auc_score(y[i], p[i]))
    return Metric(
        "AUC",
        float(roc_auc_score(y, p)),
        float(np.percentile(vals, 2.5)),
        float(np.percentile(vals, 97.5)),
    )


def expected_calibration_error(y, p, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    ece = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            ece += m.mean() * abs(y[m].mean() - p[m].mean())
    return float(ece)


def check_context(ctx: dict) -> None:
    missing = [k for k in REQUIRED_CONTEXT if not ctx.get(k)]
    if missing:
        raise CardError(f"model card context is missing required sections: {missing}")
    for k in ("out_of_scope", "limitations"):
        if not isinstance(ctx[k], list):
            raise CardError(f"{k} must be a list of statements")


def render(
    *,
    name: str,
    version: str,
    run_id: str,
    params: dict,
    y,
    p,
    group,
    ctx: dict,
    generated: str,
    prev: dict | None = None,
    gap_threshold: float = 0.03,
) -> str:
    check_context(ctx)
    auc = bootstrap_auc(y, p)
    ece = expected_calibration_error(y, p)
    metrics = [auc, Metric("Expected calibration error", ece, ece, ece)]
    slices = []
    for gname, mask in (
        ("overall", np.ones(len(y), bool)),
        ("group 0", group == 0),
        ("group 1", group == 1),
    ):
        slices.append(
            {"name": gname, "n": int(mask.sum()), "auc": float(roc_auc_score(y[mask], p[mask]))}
        )
    gap = max(s["auc"] for s in slices[1:]) - min(s["auc"] for s in slices[1:])
    changes = []
    if prev:
        for k in sorted(set(params) | set(prev["params"])):
            if str(params.get(k)) != str(prev["params"].get(k)):
                changes.append(f"parameter `{k}`: {prev['params'].get(k)} -> {params.get(k)}")
        changes.append(f"AUC: {prev['auc']:.3f} -> {auc.value:.3f}")
    summary = f"{name} version {version} scores AUC {auc.value:.3f} (95% interval {auc.lo:.3f} to {auc.hi:.3f}) on a held-out set."
    return ENV.from_string(TEMPLATE).render(
        name=name, version=version, run_id=run_id, params=dict(sorted(params.items())), ctx=ctx, metrics=metrics, slices=slices, gap=gap, gap_threshold=gap_threshold,
        n_eval=len(y), generated=generated, summary=summary, changes=changes, prev_version=prev["version"] if prev else "", 
    )  # fmt: skip
