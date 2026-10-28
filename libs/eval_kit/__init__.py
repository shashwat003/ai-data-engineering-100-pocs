"""Evaluation helpers: ranking metrics, answer checks, bootstrap confidence intervals."""

from .answers import contains_answer, support_score
from .retrieval import (
    RunMetrics,
    bootstrap_ci,
    evaluate_run,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)

__all__ = [
    "RunMetrics",
    "bootstrap_ci",
    "contains_answer",
    "evaluate_run",
    "ndcg_at_k",
    "recall_at_k",
    "reciprocal_rank",
    "support_score",
]
