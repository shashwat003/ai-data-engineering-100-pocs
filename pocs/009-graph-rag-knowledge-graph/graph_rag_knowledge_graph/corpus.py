"""Synthetic business-news corpus with known facts, paraphrases, distractors and multi-hop questions.

Facts: acquired(A, B), supplies(S, C), ceo(P, C), hq(C, City). Every fact is one passage, written in
one of several paraphrases, so extraction has to cope with wording variety (and can be degraded).
"""

from __future__ import annotations

import random

SYL = [
    "ar",
    "bel",
    "cor",
    "dun",
    "el",
    "fen",
    "gor",
    "hal",
    "iv",
    "jun",
    "kal",
    "lum",
    "mor",
    "nex",
    "or",
    "pan",
    "quo",
    "ris",
    "sol",
    "tur",
    "ul",
    "vex",
    "wyn",
    "xan",
    "yor",
    "zel",
]
CITIES = [
    "Lisbon",
    "Osaka",
    "Denver",
    "Leeds",
    "Pune",
    "Lyon",
    "Perth",
    "Bergen",
    "Quito",
    "Tampere",
]
FIRST = [
    "Ana",
    "Ben",
    "Chen",
    "Dara",
    "Eli",
    "Fay",
    "Gus",
    "Hana",
    "Ivan",
    "Jia",
    "Kai",
    "Lena",
    "Milo",
    "Nia",
    "Omar",
    "Pia",
]
LAST = [
    "Adams",
    "Brown",
    "Clark",
    "Diaz",
    "Evans",
    "Frost",
    "Garcia",
    "Hill",
    "Ito",
    "Jones",
    "Khan",
    "Lopez",
]
SUFFIX = ["Corp", "Inc", "Ltd", "Group"]

T = {
    "acquired": [
        "{a} acquired {b} in {y}.",
        "In {y}, {b} was bought by {a}.",
        "{b} became part of {a} after the {y} takeover.",
    ],
    "supplies": [
        "{a} supplies components to {b}.",
        "{b} sources its parts from {a}.",
        "{a} is the main supplier of {b}.",
    ],
    "ceo": [
        "{a} is the chief executive of {b}.",
        "{b} is led by CEO {a}.",
        "{a} runs {b} as its chief executive.",
    ],
    "hq": [
        "{a} is headquartered in {b}.",
        "The head office of {a} is in {b}.",
        "{a} has its headquarters in the city of {b}.",
    ],
}
FILLER = [
    "Analysts expect strong quarterly results across the sector this year.",
    "The industry conference will be held next spring with many speakers.",
    "Regulators published new guidance about supplier reporting and executive pay.",
    "A survey of headquarters locations shows cities competing for corporate offices.",
    "Executives discussed acquisitions and suppliers at the annual meeting.",
]


def _names(n: int, rng: random.Random) -> list[str]:
    seen, out = set(), []
    while len(out) < n:
        base = "".join(rng.choice(SYL) for _ in range(rng.choice([2, 3]))).capitalize()
        if base not in seen:
            seen.add(base)
            out.append(base)
    return out


def build(n_companies: int = 60, seed: int = 8) -> dict:
    rng = random.Random(seed)
    names = _names(n_companies, rng)
    suffix = {n: rng.choice(SUFFIX) for n in names}
    # one acquirer per target, acyclic chain-friendly structure
    order = names[:]
    rng.shuffle(order)
    facts, passages = [], []

    def alias(n):  # companies appear with or without their legal suffix
        return f"{n} {suffix[n]}" if rng.random() < 0.5 else n

    def add(kind, a, b, y=None):
        if kind == "ceo":
            at, bt = a, alias(b)
        elif kind == "hq":
            at, bt = alias(a), b
        else:
            at, bt = alias(a), alias(b)
        pid = f"p{len(passages):04d}"
        facts.append({"kind": kind, "a": a, "b": b, "y": y, "pid": pid})
        passages.append({"id": pid, "text": rng.choice(T[kind]).format(a=at, b=bt, y=y)})

    acquirer_of = {}
    for i, tgt in enumerate(order[: n_companies // 2]):
        acq = order[n_companies // 2 + i % (n_companies // 2)]
        acquirer_of[tgt] = acq
        add("acquired", acq, tgt, rng.randint(2005, 2023))
    supplier_of = {}
    for c in names:
        s = rng.choice([x for x in names if x != c])
        supplier_of[c] = s
        add("supplies", s, c)
    people = {}
    for c in names:
        p = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        while p in people.values():
            p = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        people[c] = p
        add("ceo", p, c)
        add("hq", c, rng.choice(CITIES))
    hq = {f["a"]: f["b"] for f in facts if f["kind"] == "hq"}
    for k in range(len(names)):  # distractors that share question vocabulary but carry no facts
        passages.append({"id": f"d{k:04d}", "text": rng.choice(FILLER)})
    pid = {(f["kind"], f["a"], f["b"]): f["pid"] for f in facts}

    qs = []
    for tgt, acq in sorted(acquirer_of.items()):
        q1 = {
            "hops": 1,
            "q": f"Which company acquired {tgt}?",
            "answer": acq,
            "gold": {pid[("acquired", acq, tgt)]},
        }
        q2 = {"hops": 2, "q": f"In which city is the headquarters of the company that acquired {tgt}?", "answer": hq[acq],
              "gold": {pid[("acquired", acq, tgt)], pid[("hq", acq, hq[acq])]}}  # fmt: skip
        s = supplier_of[acq]
        q3 = {"hops": 3, "q": f"Who is the chief executive of the supplier of the company that acquired {tgt}?", "answer": people[s],
              "gold": {pid[("acquired", acq, tgt)], pid[("supplies", s, acq)], pid[("ceo", people[s], s)]}}  # fmt: skip
        qs += [q1, q2, q3]
    return {
        "companies": names,
        "passages": passages,
        "facts": facts,
        "questions": qs,
        "suffix": suffix,
    }
