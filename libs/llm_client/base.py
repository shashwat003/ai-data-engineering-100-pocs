from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class LLMError(RuntimeError):
    """Backend unreachable or returned an unusable response."""


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class LLMClient(Protocol):
    def generate(
        self, prompt: str, *, model: str, temperature: float = 0.0, max_tokens: int = 256
    ) -> LLMResponse: ...


def estimate_tokens(text: str) -> int:
    """Rough ~4 chars/token heuristic for when a backend reports no token counts."""
    return max(1, round(len(text) / 4))
