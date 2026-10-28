"""Statistical regression gate: paired comparison of a candidate prompt against a baseline."""

from __future__ import annotations

from dataclasses import dataclass
from math import comb

from .suite import CATEGORIES, Case, SimModel, passed


def run_suite(model: SimModel, cases: list[Case], samples: int) -> dict[str, list[bool]]:
    """Per-case boolean outcomes for each sample (list of length `samples`)."""
    return {c.id: [passed(c, model.generate(c, s)) for s in range(samples)] for c in cases}


def majority(outcomes: list[bool]) -> bool:
    return sum(outcomes) * 2 > len(outcomes)


def binom_sf_two_sided_less(k: int, n: int) -> float:
    """P(X <= k) for X ~ Binomial(n, 0.5): one-sided exact sign-test p-value."""
    return sum(comb(n, i) for i in range(0, k + 1)) / 2**n if n else 1.0


@dataclass
class GateResult:
    passed: bool
    reasons: list[str]
    base_rate: float
    cand_rate: float
    regressed: int  # cases baseline passed, candidate failed
    improved: int
    p_regression: float
    by_category: dict[str, tuple[float, float]]


def compare(base: dict[str, list[bool]], cand: dict[str, list[bool]], cases: list[Case], *, alpha: float = 0.05,
            max_drop: float = 0.10, min_cat_cases: int = 10) -> GateResult:  # fmt: skip
    """Fail when (a) a hard case ever fails in the candidate, (b) the exact sign test over
    discordant cases shows a significant regression, or (c) a category drops by more than max_drop and that drop is itself significant (Bonferroni)."""
    reasons = []
    b = {c.id: majority(base[c.id]) for c in cases}
    k = {c.id: majority(cand[c.id]) for c in cases}
    regressed = sum(b[c.id] and not k[c.id] for c in cases)
    improved = sum(k[c.id] and not b[c.id] for c in cases)
    p = binom_sf_two_sided_less(
        improved, regressed + improved
    )  # P(<= improved) if regressions and improvements were equally likely
    for c in cases:
        if c.hard and not all(cand[c.id]):
            reasons.append(
                f"hard case {c.id} failed in {cand[c.id].count(False)}/{len(cand[c.id])} samples"
            )
            break
    if p < alpha and regressed > improved:
        reasons.append(
            f"significant regression: {regressed} regressed vs {improved} improved (sign test p={p:.3f})"
        )
    by_cat = {}
    for cat in CATEGORIES:
        cs = [c for c in cases if c.category == cat]
        rb, rk = sum(b[c.id] for c in cs) / len(cs), sum(k[c.id] for c in cs) / len(cs)
        by_cat[cat] = (round(rb, 3), round(rk, 3))
        reg = sum(b[c.id] and not k[c.id] for c in cs)
        imp = sum(k[c.id] and not b[c.id] for c in cs)
        p_cat = binom_sf_two_sided_less(imp, reg + imp)
        # a category must both drop materially and be significant after Bonferroni over categories
        if len(cs) >= min_cat_cases and rb - rk > max_drop and p_cat < alpha / len(CATEGORIES):
            reasons.append(f"{cat} dropped {rb:.0%} -> {rk:.0%} (sign test p={p_cat:.3f})")
    return GateResult(not reasons, reasons, sum(b.values()) / len(cases), sum(k.values()) / len(cases),
                      regressed, improved, p, by_cat)  # fmt: skip
