"""Benchmark tasks with deterministic checkers. Synthetic prompts; no external datasets."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

Checker = Callable[[str], bool]


@dataclass(frozen=True)
class Task:
    id: str
    category: str
    prompt: str
    check: Checker
    gold: str  # a correct reply (used by the fake backend only)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def exact(answer: str) -> Checker:
    return lambda out: (
        _norm(out).split()[-1:] == _norm(answer).split()[-1:] and _norm(answer) in _norm(out)
    )


def json_with(**expected) -> Checker:
    def chk(out: str) -> bool:
        m = re.search(r"\{.*\}", out, re.S)
        if not m:
            return False
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError:
            return False
        return all(d.get(k) == v for k, v in expected.items())

    return chk


def max_words(n: int, must: str) -> Checker:
    return lambda out: 0 < len(out.split()) <= n and must.lower() in out.lower()


def build_tasks() -> list[Task]:
    t: list[Task] = []
    for i, (a, b) in enumerate([(17, 25), (48, 36), (91, 14), (63, 27), (55, 45), (72, 19)]):
        t.append(
            Task(
                f"math{i}",
                "arithmetic",
                f"What is {a} + {b}? Reply with only the number.",
                exact(str(a + b)),
                str(a + b),
            )
        )
    for i, (txt, lab) in enumerate(
        [
            ("The delivery was late and the box was damaged.", "negative"),
            ("Fantastic service, will buy again!", "positive"),
            ("It arrived on Tuesday.", "neutral"),
            ("Support never answered my emails.", "negative"),
            ("Great value and fast shipping.", "positive"),
            ("The package contains one cable.", "neutral"),
        ]
    ):
        t.append(
            Task(
                f"cls{i}",
                "classification",
                f"Classify the sentiment as positive, negative or neutral. Reply with one word.\nText: {txt}",
                exact(lab),
                lab,
            )
        )
    for i, (name, city, age) in enumerate(
        [
            ("Ana Ng", "Lisbon", 34),
            ("Ben Ito", "Osaka", 41),
            ("Chen Wu", "Leeds", 29),
            ("Dara Roy", "Pune", 52),
            ("Eli Fox", "Perth", 38),
            ("Fay Lee", "Bergen", 47),
        ]
    ):
        t.append(
            Task(
                f"ext{i}",
                "extraction",
                f"Return JSON with keys name, city, age.\nText: {name} is {age} years old and lives in {city}.",
                json_with(name=name, city=city, age=age),
                json.dumps({"name": name, "city": city, "age": age}),
            )
        )
    for i, (word, n) in enumerate(
        [
            ("invoice", 12),
            ("warehouse", 10),
            ("pipeline", 12),
            ("schema", 8),
            ("latency", 10),
            ("quorum", 8),
        ]
    ):
        t.append(
            Task(
                f"con{i}",
                "constraint",
                f"Write one sentence about '{word}' in at most {n} words and include the word '{word}'.",
                max_words(n, word),
                f"A {word} matters here.",
            )
        )
    for i, (q, a) in enumerate(
        [
            ("Which is larger, 3/4 or 2/3? Reply with the fraction.", "3/4"),
            (
                "If all bloops are razzies and all razzies are lazzies, are all bloops lazzies? Reply yes or no.",
                "yes",
            ),
            ("What day comes two days after Friday? Reply with the day.", "sunday"),
            ("How many minutes are in 2.5 hours? Reply with the number.", "150"),
            ("Is 91 prime? Reply yes or no.", "no"),
            ("What is the next number: 2, 6, 12, 20, ?", "30"),
        ]
    ):
        t.append(Task(f"rsn{i}", "reasoning", q, exact(a), a))
    return t
