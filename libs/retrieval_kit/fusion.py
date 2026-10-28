from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], k: int = 60
) -> list[tuple[str, float]]:
    """RRF: score(d) = sum over rankings of 1 / (k + rank). Needs only ranks, not scores."""
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] += 1.0 / (k + rank)
    # stable sort: ties keep first-seen order, so a zero-weight list leaves the other order intact
    return sorted(scores.items(), key=lambda kv: -kv[1])


def _max_normalise(results: Sequence[tuple[str, float]]) -> dict[str, float]:
    """Scale scores to [0, 1] by the list maximum (negatives clip to 0).

    Max-normalisation rather than min-max: min-max maps the weakest hit of a short result list to
    0, which makes a genuine match tie with documents the retriever never returned.
    """
    top = max((s for _, s in results), default=0.0)
    if top <= 0:
        return {d: 0.0 for d, _ in results}
    return {d: max(s, 0.0) / top for d, s in results}


def weighted_fusion(
    results: Sequence[Sequence[tuple[str, float]]], weights: Sequence[float]
) -> list[tuple[str, float]]:
    """Weighted sum of max-normalised scores; a doc missing from a list contributes 0."""
    if len(results) != len(weights):
        raise ValueError("one weight per result list is required")
    total: dict[str, float] = defaultdict(float)
    for res, w in zip(results, weights, strict=True):
        for doc_id, s in _max_normalise(res).items():
            total[doc_id] += w * s
    return sorted(total.items(), key=lambda kv: -kv[1])
