"""Run routing policies over a task set and compare cost, accuracy and latency."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from eval_kit import bootstrap_ci
from pocs_common import CostLedger, ModelPrice, markdown_table, percentile

from .models import LLM_PROFILE, SLM_PROFILE, CachedModel, Model, SimulatedModel
from .router import CascadeRouter, FeatureRouter, OracleRouter, Router, SingleModelRouter
from .tasks import Task, is_correct

THRESHOLDS = tuple(round(i * 0.05, 2) for i in range(21))  # 0.00 (never escalate) .. 1.00 (always)


@dataclass
class PolicyResult:
    name: str
    n: int
    accuracy: float
    cost_usd: float
    cost_per_1k: float
    escalation_rate: float
    degraded_rate: float
    mean_latency_ms: float
    p95_latency_ms: float
    tokens_in: int = 0
    tokens_out: int = 0
    threshold: float | None = None
    correct: list[bool] = field(default_factory=list, repr=False)

    def summary(self) -> dict:
        d = dataclasses.asdict(self)
        d.pop("correct")
        return d


def run_policy(
    router: Router,
    tasks: Sequence[Task],
    prices: Mapping[str, ModelPrice],
    budget_usd: float | None = None,
) -> PolicyResult:
    ledger = CostLedger(prices, budget_usd)
    results = [router.route(t, ledger) for t in tasks]
    correct = [is_correct(t, r.answer) for t, r in zip(tasks, results, strict=True)]
    latencies = [r.latency_ms for r in results]
    n = len(tasks)
    return PolicyResult(
        name=router.name,
        n=n,
        accuracy=sum(correct) / n,
        cost_usd=ledger.total,
        cost_per_1k=ledger.total / n * 1000,
        escalation_rate=sum(r.escalated for r in results) / n,
        degraded_rate=sum(r.degraded for r in results) / n,
        mean_latency_ms=sum(latencies) / n,
        p95_latency_ms=percentile(latencies, 95),
        tokens_in=ledger.input_tokens,
        tokens_out=ledger.output_tokens,
        threshold=getattr(router, "threshold", None),
        correct=correct,
    )


def threshold_sweep(
    small: Model,
    large: Model,
    tasks: Sequence[Task],
    prices: Mapping[str, ModelPrice],
    thresholds: Sequence[float] = THRESHOLDS,
) -> list[PolicyResult]:
    return [run_policy(CascadeRouter(small, large, t), tasks, prices) for t in thresholds]


def pick_operating_point(
    sweep: Sequence[PolicyResult], baseline_accuracy: float, max_drop: float
) -> PolicyResult | None:
    """Cheapest cascade whose accuracy is within max_drop of the baseline (ties: lower threshold)."""
    ok = [r for r in sweep if r.accuracy >= baseline_accuracy - max_drop]
    return min(ok, key=lambda r: (r.cost_usd, r.threshold or 0.0)) if ok else None


def paired_accuracy_diff(
    a: Sequence[bool], b: Sequence[bool], seed: int = 0
) -> tuple[float, float, float]:
    """Mean per-task correctness difference (a - b) with a 95% paired bootstrap CI."""
    diffs = [float(x) - float(y) for x, y in zip(a, b, strict=True)]
    lo, hi = bootstrap_ci(diffs, seed=seed)
    return sum(diffs) / len(diffs), lo, hi


def run_experiment(
    tasks: Sequence[Task],
    prices: Mapping[str, ModelPrice],
    *,
    small: Model | None = None,
    large: Model | None = None,
    max_drop: float = 0.02,
    feature_cutoff: float = 0.5,
    study_calibration: bool = True,
) -> dict:
    small = CachedModel(small or SimulatedModel(SLM_PROFILE))
    large = CachedModel(large or SimulatedModel(LLM_PROFILE))
    always_small = run_policy(SingleModelRouter(small, "always_small"), tasks, prices)
    always_large = run_policy(SingleModelRouter(large, "always_large"), tasks, prices)
    sweep = threshold_sweep(small, large, tasks, prices)
    best = pick_operating_point(sweep, always_large.accuracy, max_drop)

    policies = [always_small, always_large]
    out: dict = {"max_accuracy_drop": max_drop, "operating_point": None, "budget_guard": None}
    if best is not None:
        policies.append(dataclasses.replace(best, name="cascade_best"))
        diff, lo, hi = paired_accuracy_diff(best.correct, always_large.correct)
        out["operating_point"] = {
            "threshold": best.threshold,
            "cost_saving_vs_always_large": 1 - best.cost_usd / always_large.cost_usd,
            "accuracy_diff_vs_always_large": {"mean": diff, "ci95": [lo, hi]},
        }
        # Budget guard: cap spend at half of what the unconstrained cascade needs.
        budget = best.cost_usd / 2
        guarded = run_policy(
            CascadeRouter(small, large, best.threshold, name="cascade_best+budget"),
            tasks,
            prices,
            budget_usd=budget,
        )
        policies.append(guarded)
        out["budget_guard"] = {"budget_usd": budget, **guarded.summary()}
    policies.append(
        run_policy(
            FeatureRouter(small, large, feature_cutoff, name="feature_router"), tasks, prices
        )
    )
    if best is not None:
        policies.append(
            run_policy(
                FeatureRouter(
                    small,
                    large,
                    0.7,
                    cascade_threshold=best.threshold,
                    name="feature_then_cascade",
                ),
                tasks,
                prices,
            )
        )
    policies.append(run_policy(OracleRouter(small, large), tasks, prices))

    out["policies"] = [p.summary() for p in policies]
    out["sweep"] = [p.summary() for p in sweep]
    out["calibration_study"] = []
    if study_calibration and isinstance(small.inner, SimulatedModel):
        for cal in (0.0, 0.1, 0.2, 0.35, 0.5):
            sm = CachedModel(SimulatedModel(dataclasses.replace(SLM_PROFILE, calibration=cal)))
            sw = threshold_sweep(sm, large, tasks, prices)
            op = pick_operating_point(sw, always_large.accuracy, max_drop)
            row = {"small_calibration": cal, "feasible": op is not None}
            if op:
                row |= {
                    "threshold": op.threshold,
                    "accuracy": op.accuracy,
                    "escalation_rate": op.escalation_rate,
                    "cost_saving_vs_always_large": 1 - op.cost_usd / always_large.cost_usd,
                }
            out["calibration_study"].append(row)
    return out


def render_markdown(results: dict, meta: dict) -> str:
    real = meta["backend"] != "sim"
    cost_hdr = "hypothetical cost $/1k req" if real else "cost $/1k req"
    saving_hdr = "token-cost saving (hypothetical prices)" if real else "cost saving"
    lines = [
        "# Results: measured locally with Ollama (do not edit)"
        if real
        else "# Results: simulated (generated by `make demo`; do not edit)",
        "",
        f"- backend: **{meta['backend']}**, {meta['n_tasks']} tasks, seed {meta['seed']}, "
        f"small=`{meta['small']}`, large=`{meta['large']}`",
    ]
    if meta["backend"] == "sim":
        lines.append(
            "- **Simulation.** Model accuracy, confidence and latency are assumed profiles "
            "(see README), and prices are illustrative placeholders. These numbers show the "
            "routing mechanism's behaviour under stated assumptions, not real model quality or "
            "real bills."
        )
    if real:
        det = meta.get("ollama", {})
        lines += [
            "- **Measured on real local models.** Accuracy, latency and token counts are measurements "
            "from this run on this machine. Cost columns apply the hypothetical per-token prices in "
            "`config/prices.json` to the measured tokens; local inference has no per-token bill, "
            "so they are an API-equivalent estimate, not savings on an invoice.",
            f"- Ollama {det.get('ollama_version')}; models: "
            + "; ".join(f"`{k}` {v}" for k, v in det.get("models", {}).items()),
            "- Malformed replies (missing ANSWER/CONFIDENCE format): "
            + ", ".join(f"`{k}` {v:.1%}" for k, v in meta.get("malformed_rate", {}).items()),
            f"- Only {meta['n_tasks']} tasks: intervals are wide; treat differences of a few "
            "points as noise.",
        ]
    pol_rows = [
        [
            p["name"],
            p["accuracy"],
            p["cost_per_1k"],
            (p["tokens_in"] + p["tokens_out"]) / p["n"],
            p["escalation_rate"],
            p["degraded_rate"],
            p["mean_latency_ms"],
            p["p95_latency_ms"],
        ]
        for p in results["policies"]
    ]
    lines += [
        "",
        "## Policies",
        "",
        markdown_table(
            [
                "policy",
                "accuracy",
                cost_hdr,
                "tokens/req",
                "escalation",
                "degraded",
                "mean ms",
                "p95 ms",
            ],
            pol_rows,
        ),
    ]
    op = results["operating_point"]
    if op:
        d = op["accuracy_diff_vs_always_large"]
        lines += [
            "",
            f"## Operating point (cheapest cascade within {results['max_accuracy_drop']:.0%} "
            "accuracy of always_large)",
            "",
            f"- threshold **{op['threshold']:.2f}**, {saving_hdr} vs always_large "
            f"**{op['cost_saving_vs_always_large']:.1%}**",
            f"- accuracy difference vs always_large: {d['mean']:+.3f} "
            f"(95% paired bootstrap CI {d['ci95'][0]:+.3f} to {d['ci95'][1]:+.3f})",
        ]
    else:
        lines += ["", "No cascade threshold met the accuracy constraint."]
    sweep_rows = [
        [
            s["threshold"],
            s["accuracy"],
            s["cost_per_1k"],
            s["escalation_rate"],
            s["mean_latency_ms"],
        ]
        for s in results["sweep"][::2]
    ]
    lines += [
        "",
        "## Threshold sweep (every other threshold shown; full data in results.json)",
        "",
        markdown_table(["threshold", "accuracy", cost_hdr, "escalation", "mean ms"], sweep_rows),
    ]
    if results["calibration_study"]:
        cal_rows = [
            [
                c["small_calibration"],
                *(
                    [
                        c["threshold"],
                        c["accuracy"],
                        c["escalation_rate"],
                        c["cost_saving_vs_always_large"],
                    ]
                    if c["feasible"]
                    else ["-", "-", "-", "infeasible"]
                ),
            ]
            for c in results["calibration_study"]
        ]
        lines += [
            "",
            "## Sensitivity: how informative the small model's confidence is",
            "",
            "Calibration 0 means confidence carries no signal about correctness.",
            "",
            markdown_table(
                ["calibration", "threshold", "accuracy", "escalation", "cost saving"], cal_rows
            ),
        ]
    bg = results.get("budget_guard")
    if bg:
        lines += [
            "",
            "## Budget guard",
            "",
            f"With spend capped at ${bg['budget_usd']:.6f} (half the unconstrained cascade cost), "
            f"accuracy was {bg['accuracy']:.3f}, {bg['degraded_rate']:.1%} of requests were "
            "degraded to the small model's answer once the budget ran out.",
        ]
    return "\n".join(lines) + "\n"
