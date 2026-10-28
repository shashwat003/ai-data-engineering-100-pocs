"""Synthetic product reviews with sentiment labels, including hard cases (negation, contrast, sarcasm-free).

Clauses come from small pools; the label follows the clause polarity logic. 5% label noise is added so
no model can reach 100%. The `hard` flag marks reviews whose polarity needs word order (negation or
contrast), where bag-of-words models are expected to struggle.
"""

from __future__ import annotations

import random

POS = [
    "works great",
    "love it",
    "excellent quality",
    "very happy with this",
    "fast shipping and perfect fit",
    "does exactly what I wanted",
    "would buy again",
]
NEG = [
    "stopped working",
    "terrible quality",
    "very disappointed",
    "waste of money",
    "arrived broken",
    "does not do what it claims",
    "would not buy again",
]
NEU = [
    "it is a standard model",
    "arrived on tuesday",
    "the box was medium sized",
    "I ordered the blue one",
    "the manual has twelve pages",
]
NEGATED_POS = [
    "not good at all",
    "not happy with this",
    "does not work great",
    "never works properly",
    "not what I hoped for",
]
NEGATED_NEG = [
    "not bad at all",
    "no complaints",
    "nothing wrong with it",
    "not disappointed",
    "never had a problem",
]
CONTRAST_POS_FIRST = ["{p} at first, but {n}", "{p}, however later {n}"]
CONTRAST_NEG_FIRST = ["{n} in the packaging, but {p}", "{n} at first, yet overall {p}"]
LABELS = ("negative", "neutral", "positive")


def make_review(rng: random.Random) -> tuple[str, str, bool]:
    kind = rng.choices(
        ["pos", "neg", "neu", "negpos", "negneg", "c_neg", "c_pos"],
        weights=[22, 22, 14, 10, 10, 11, 11],
    )[0]
    p, n, u = rng.choice(POS), rng.choice(NEG), rng.choice(NEU)
    if kind == "pos":
        text, label, hard = f"{p}. {u}", "positive", False
    elif kind == "neg":
        text, label, hard = f"{n}. {u}", "negative", False
    elif kind == "neu":
        text, label, hard = f"{u}. {rng.choice(NEU)}", "neutral", False
    elif kind == "negpos":
        text, label, hard = f"{rng.choice(NEGATED_POS)}. {u}", "negative", True
    elif kind == "negneg":
        text, label, hard = f"{rng.choice(NEGATED_NEG)}. {u}", "positive", True
    elif kind == "c_neg":  # positive first, negative conclusion
        text, label, hard = (
            rng.choice(CONTRAST_POS_FIRST).format(p=p, n=n) + f". {u}",
            "negative",
            True,
        )
    else:  # negative first, positive conclusion
        text, label, hard = (
            rng.choice(CONTRAST_NEG_FIRST).format(p=p, n=n) + f". {u}",
            "positive",
            True,
        )
    return text, label, hard


def generate(n: int, seed: int, noise: float = 0.05) -> list[dict]:
    rng, nrng = (
        random.Random(seed),
        random.Random(seed + 10_000),
    )  # separate stream: noise never shifts the text stream
    rows = []
    for _ in range(n):
        text, label, hard = make_review(rng)
        if nrng.random() < noise:
            label = nrng.choice([x for x in LABELS if x != label])
        rows.append({"text": text, "label": label, "hard": hard})
    return rows
