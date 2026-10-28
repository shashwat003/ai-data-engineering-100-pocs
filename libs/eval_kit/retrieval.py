from __future__ import annotations

import math
import random
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass


def recall_at_k(ranked: Sequence[str], relevant: Collection[str], k: int) -> float:
    return len(set(ranked[:k]) & set(relevant)) / len(relevant)


def reciprocal_rank(ranked: Sequence[str], relevant: Collection[str], k: int = 10) -> float:
    for rank, doc_id in enumerate(ranked[:k], start=1):
        if doc_id in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked: Sequence[str], relevant: Collection[str], k: int = 10) -> float:
    rel = set(relevant)
    dcg = sum(1.0 / math.log2(r + 1) for r, d in enumerate(ranked[:k], start=1) if d in rel)
    ideal = sum(1.0 / math.log2(r + 1) for r in range(1, min(len(rel), k) + 1))
    return dcg / ideal


@dataclass(frozen=True)
class RunMetrics:
    aggregate: dict[str, float]
    per_query: dict[str, dict[str, float]]


def evaluate_run(
    run: Mapping[str, Sequence[str]],
    qrels: Mapping[str, Collection[str]],
    *,
    ks: Sequence[int] = (1, 3, 5),
    mrr_k: int = 10,
    ndcg_k: int = 10,
) -> RunMetrics:
    """Score a retrieval run ({query_id: ranked doc ids}) against binary relevance judgements."""
    per_query: dict[str, dict[str, float]] = {}
    for qid, relevant in qrels.items():
        ranked = run.get(qid, [])
        row = {f"recall@{k}": recall_at_k(ranked, relevant, k) for k in ks}
        row[f"mrr@{mrr_k}"] = reciprocal_rank(ranked, relevant, mrr_k)
        row[f"ndcg@{ndcg_k}"] = ndcg_at_k(ranked, relevant, ndcg_k)
        per_query[qid] = row
    metric_names = next(iter(per_query.values())).keys() if per_query else []
    aggregate = {m: sum(r[m] for r in per_query.values()) / len(per_query) for m in metric_names}
    return RunMetrics(aggregate, per_query)


def bootstrap_ci(
    values: Sequence[float], *, n_boot: int = 1000, alpha: float = 0.05, seed: int = 0
) -> tuple[float, float]:
    """Percentile bootstrap confidence interval of the mean (deterministic given seed)."""
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot))
    return means[int(n_boot * alpha / 2)], means[int(n_boot * (1 - alpha / 2)) - 1]
