"""Token-cost accounting with an optional hard budget. Prices are always supplied by the caller."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class BudgetExceeded(RuntimeError):
    """Raised by CostLedger.record(enforce=True) when a call would pass the budget."""


@dataclass(frozen=True)
class ModelPrice:
    """USD per one million tokens."""

    input_per_mtok: float
    output_per_mtok: float

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok) / 1e6


def load_prices(path: str | Path) -> dict[str, ModelPrice]:
    """Load {model: {input_per_mtok, output_per_mtok}} from JSON; keys starting with '_' are notes."""
    raw = json.loads(Path(path).read_text())
    return {
        name: ModelPrice(float(v["input_per_mtok"]), float(v["output_per_mtok"]))
        for name, v in raw.items()
        if not name.startswith("_")
    }


class CostLedger:
    def __init__(self, prices: Mapping[str, ModelPrice], budget_usd: float | None = None):
        self.prices = dict(prices)
        self.budget_usd = budget_usd
        self.total = 0.0
        self._by_model: dict[str, float] = defaultdict(float)
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def cost_of(self, model: str, input_tokens: int, output_tokens: int) -> float:
        if model not in self.prices:
            raise KeyError(f"no price configured for model {model!r}")
        return self.prices[model].cost(input_tokens, output_tokens)

    def exhausted(self) -> bool:
        return self.budget_usd is not None and self.total >= self.budget_usd

    def record(
        self, model: str, input_tokens: int, output_tokens: int, *, enforce: bool = False
    ) -> float:
        cost = self.cost_of(model, input_tokens, output_tokens)
        if enforce and self.budget_usd is not None and self.total + cost > self.budget_usd:
            raise BudgetExceeded(
                f"{self.total + cost:.6f} would exceed budget {self.budget_usd:.6f}"
            )
        self.total += cost
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self._by_model[model] += cost
        self.calls += 1
        return cost

    def by_model(self) -> dict[str, float]:
        return dict(self._by_model)
