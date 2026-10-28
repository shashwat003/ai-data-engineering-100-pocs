"""Synthetic support questions: paraphrase clusters (should hit) and near-miss twins (must NOT hit)."""

from __future__ import annotations

import random

TEMPLATES = [
    (
        "How do I get a refund for order {n}?",
        [
            "What is the process to get a refund for order {n}?",
            "I want my money back for order {n}, how do I do that?",
            "Refund steps for order {n}, please",
        ],
    ),
    (
        "Where is my package for order {n}?",
        [
            "Where is the package for order {n} right now?",
            "Track my package for order {n}",
            "Order {n}: where is my package?",
        ],
    ),
    (
        "Can I change the shipping address of order {n}?",
        [
            "Is it possible to change the shipping address for order {n}?",
            "How to change the shipping address on order {n}?",
            "Change shipping address order {n} possible?",
        ],
    ),
    (
        "What is the warranty period for {p}?",
        [
            "How long is the warranty on {p}?",
            "Warranty period of {p}?",
            "What warranty does {p} have?",
        ],
    ),
    (
        "Does {p} work with {q}?",
        [
            "Is {p} compatible with {q}?",
            "Will {p} work together with {q}?",
            "{p} and {q}: compatible?",
        ],
    ),
]
PRODUCTS = ["Atlas", "Beacon", "Cobalt", "Ember", "Falcon", "Granite"]


def make(n_clusters: int = 120, seed: int = 1):
    """Returns (seed_questions, paraphrases[(question, index of its seed)], near_misses[(question, index of the seed it resembles)])."""
    rng = random.Random(seed)
    seeds, paras, twins = [], [], []
    for _ in range(n_clusters):
        t, ps = TEMPLATES[rng.randrange(len(TEMPLATES))]
        n, p, q = rng.randint(1000, 9999), rng.choice(PRODUCTS), rng.choice(PRODUCTS)
        fmt = dict(n=n, p=p, q=q)
        idx = len(seeds)
        seeds.append(t.format(**fmt))
        paras.append((rng.choice(ps).format(**fmt), idx))
        # twin: same wording, different order number / product (the answer would differ)
        if "{n}" in t:
            twins.append((t.format(n=n + rng.randint(1, 9), p=p, q=q), idx))
        elif "{q}" in t:
            other = rng.choice([x for x in PRODUCTS if x != q])
            twins.append((t.format(n=n, p=p, q=other), idx))
        else:
            other = rng.choice([x for x in PRODUCTS if x != p])
            twins.append((t.format(n=n, p=other, q=q), idx))
    return seeds, paras, twins
