"""Synthetic, auto-gradable tasks with a known difficulty, plus a prompt-only difficulty heuristic.

Two task kinds that real models can also attempt:
- classify: assign a support ticket to a category. Harder when the text mixes in phrases that
  belong to other categories (the gold label is the category with the most phrases).
- arith: apply a chain of 1-4 arithmetic steps to a starting number.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

CATEGORIES: dict[str, tuple[str, ...]] = {
    "billing": ("invoice", "refund", "charged twice", "payment failed", "receipt"),
    "outage": ("site is down", "timeout", "service unreachable", "crashed", "error 500"),
    "access": ("password reset", "locked out", "cannot log in", "permission denied", "2fa code"),
    "feature": (
        "feature request",
        "would like support for",
        "please add",
        "on the roadmap",
        "enhancement",
    ),
}
FILLERS = ("Hello team,", "Urgent:", "Quick question:", "Following up:", "Hi,")


@dataclass(frozen=True)
class Task:
    task_id: str
    kind: str  # "classify" | "arith"
    prompt: str
    gold: str
    difficulty: float  # ground-truth difficulty in [0, 1], used only by the simulator
    choices: tuple[str, ...] = ()


def _classify_task(rng: random.Random, i: int) -> Task:
    gold = rng.choice(list(CATEGORIES))
    n_other = rng.choice((0, 1, 2))
    others = rng.sample([c for c in CATEGORIES if c != gold], n_other)
    phrases = rng.sample(CATEGORIES[gold], 2) + [rng.choice(CATEGORIES[o]) for o in others]
    rng.shuffle(phrases)
    text = f"{rng.choice(FILLERS)} customer reports {' and '.join(phrases)}."
    prompt = (
        f"Classify the support ticket into one of: {', '.join(CATEGORIES)}.\nTicket: {text}\n"
        "Reply with the category name only."
    )
    difficulty = 0.15 + 0.35 * n_other + rng.uniform(-0.05, 0.05)
    return Task(f"t{i:04d}", "classify", prompt, gold, difficulty, tuple(CATEGORIES))


def _arith_task(rng: random.Random, i: int) -> Task:
    steps = rng.randint(1, 4)
    value = start = rng.randint(2, 30)
    lines = [f"Start with {start}."]
    for _ in range(steps):
        op = rng.choice(("add", "subtract", "multiply"))
        n = rng.randint(2, 9)
        value = value + n if op == "add" else value - n if op == "subtract" else value * n
        lines.append(f"{op.capitalize()} {'by ' if op == 'multiply' else ''}{n}.")
    prompt = " ".join(lines) + " What is the final number? Reply with a single integer."
    difficulty = 0.1 + 0.25 * (steps - 1) + rng.uniform(-0.05, 0.05)
    return Task(f"t{i:04d}", "arith", prompt, str(value), difficulty)


def generate_tasks(n: int = 400, seed: int = 11) -> list[Task]:
    rng = random.Random(seed)
    return [(_classify_task if i % 2 == 0 else _arith_task)(rng, i) for i in range(n)]


def is_correct(task: Task, answer: str) -> bool:
    answer = answer.strip().lower()
    if task.kind == "arith":
        return re.fullmatch(r"-?\d+", answer) is not None and int(answer) == int(task.gold)
    return answer == task.gold


def heuristic_difficulty(prompt: str) -> float:
    """Predict difficulty from the prompt text alone (no labels).

    Tuned to this synthetic generator, so it flatters the feature router; a production router
    would learn this mapping from logged outcomes.
    """
    text = prompt.lower()
    if "classify the support ticket" in text:
        ticket = text.split("ticket:", 1)[1]
        n_cats = sum(any(p in ticket for p in phrases) for phrases in CATEGORIES.values())
        return 0.15 + 0.35 * (max(n_cats, 1) - 1)
    steps = len(re.findall(r"\b(add|subtract|multiply)\b", text))
    return 0.1 + 0.25 * (max(steps, 1) - 1)
