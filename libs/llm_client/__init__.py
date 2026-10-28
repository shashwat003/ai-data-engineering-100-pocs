"""One small interface over local LLM backends (stdlib only)."""

from .base import LLMClient, LLMError, LLMResponse, estimate_tokens
from .ollama import OllamaClient, model_matches
from .stub import StubClient

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMResponse",
    "OllamaClient",
    "StubClient",
    "estimate_tokens",
    "model_matches",
]
