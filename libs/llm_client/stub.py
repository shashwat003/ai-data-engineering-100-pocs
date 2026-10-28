from __future__ import annotations

from collections.abc import Callable

from .base import LLMResponse, estimate_tokens


class StubClient:
    """Deterministic fake backend for tests: `StubClient(lambda prompt: "answer")`."""

    def __init__(self, respond: Callable[[str], str] | str = "ok"):
        self._respond = respond if callable(respond) else (lambda _p: respond)
        self.calls: list[str] = []

    def generate(
        self,
        prompt: str,
        *,
        model: str = "stub",
        temperature: float = 0.0,
        max_tokens: int = 256,
        seed: int | None = None,
    ) -> LLMResponse:
        self.calls.append(prompt)
        text = self._respond(prompt)
        return LLMResponse(text, model, estimate_tokens(prompt), estimate_tokens(text), 0.0)
