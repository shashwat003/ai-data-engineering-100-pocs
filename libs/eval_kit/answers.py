from __future__ import annotations

import re
from collections.abc import Sequence

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an and are as at be by for from in is it of on or the to was with".split())


def _norm(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def contains_answer(text: str, gold: str) -> bool:
    """True if the gold answer appears (normalised, whole-word) in the text."""
    return f" {_norm(gold)} " in f" {_norm(text)} "


def support_score(answer: str, contexts: Sequence[str]) -> float:
    """Lexical groundedness: share of the answer's content words found in the contexts.

    A cheap, deterministic proxy. It detects invented content words but not wrong claims
    assembled from supported words, so treat it as a smoke test, not proof of faithfulness.
    """
    words = [w for w in _WORD.findall(answer.lower()) if w not in _STOP]
    if not words:
        return 1.0
    context_words = set(_WORD.findall(" ".join(contexts).lower()))
    return sum(w in context_words for w in words) / len(words)
