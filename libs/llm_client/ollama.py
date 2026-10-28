"""Client for a local Ollama server (https://ollama.com). Free, no API key, stdlib HTTP only."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Sequence

from .base import LLMError, LLMResponse, estimate_tokens


def model_matches(installed: str, wanted: str) -> bool:
    """Ollama lists 'llama3.2:3b' and 'foo:latest'; a bare name means ':latest'."""
    return installed == wanted or (":" not in wanted and installed == f"{wanted}:latest")


class OllamaClient:
    def __init__(self, base_url: str = "http://localhost:11434", timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base_url + path, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                return json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LLMError(f"Ollama request to {self.base_url}{path} failed: {exc}") from exc

    def version(self) -> str | None:
        return self._request("/api/version").get("version")

    def list_models(self) -> list[dict]:
        """Installed models: [{'name', 'digest', 'size', 'details': {...}}, ...]."""
        return self._request("/api/tags").get("models", [])

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 256,
        system: str | None = None,
        seed: int | None = None,
    ) -> LLMResponse:
        options: dict = {"temperature": temperature, "num_predict": max_tokens}
        if seed is not None:
            options["seed"] = seed
        payload: dict = {"model": model, "prompt": prompt, "stream": False, "options": options}
        if system:
            payload["system"] = system
        start = time.perf_counter()
        data = self._request("/api/generate", payload)
        latency_ms = (time.perf_counter() - start) * 1000.0
        if "response" not in data:
            raise LLMError(f"unexpected Ollama response keys: {sorted(data)}")
        text = data["response"]
        return LLMResponse(
            text=text,
            model=model,
            input_tokens=int(data.get("prompt_eval_count") or estimate_tokens(prompt)),
            output_tokens=int(data.get("eval_count") or estimate_tokens(text)),
            latency_ms=latency_ms,
        )

    def embed(self, texts: Sequence[str], *, model: str) -> list[list[float]]:
        data = self._request("/api/embed", {"model": model, "input": list(texts)})
        if "embeddings" not in data:
            raise LLMError(f"unexpected Ollama response keys: {sorted(data)}")
        return data["embeddings"]
