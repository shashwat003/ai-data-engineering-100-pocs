from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "a an and are as at be by for from how in is it of on or that the this to was what when "
    "which who with does do did".split()
)


def tokenize(text: str, *, remove_stopwords: bool = True) -> list[str]:
    tokens = _TOKEN.findall(text.lower())
    return [t for t in tokens if t not in STOPWORDS] if remove_stopwords else tokens


def char_ngrams(token: str, n: int = 3) -> list[str]:
    padded = f"#{token}#"
    return [padded[i : i + n] for i in range(len(padded) - n + 1)]
