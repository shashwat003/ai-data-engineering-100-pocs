"""Model backends. SimulatedModel is a labelled simulation; OllamaModel calls a real local model."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import NamedTuple, Protocol

from llm_client import LLMError, estimate_tokens, model_matches
from pocs_common import stable_normal, stable_unit

from .tasks import Task


@dataclass(frozen=True)
class ModelOutput:
    answer: str
    confidence: float  # the model's own confidence signal in [0, 1]
    input_tokens: int
    output_tokens: int
    latency_ms: float


class Model(Protocol):
    name: str

    def generate(self, task: Task) -> ModelOutput: ...


@dataclass(frozen=True)
class SimProfile:
    """Assumed behaviour of a simulated model. All numbers are assumptions, not measurements."""

    name: str
    capability: float  # difficulty at which P(correct) = 0.5
    sharpness: float  # how fast P(correct) falls past capability
    calibration: float  # 0 = confidence carries no signal; larger = separates right from wrong
    noise_sd: float
    base_latency_ms: float
    ms_per_output_token: float
    overhead_output_tokens: int  # reasoning / verbosity


SLM_PROFILE = SimProfile("sim-slm-3b", 0.45, 8.0, 0.35, 0.22, 60.0, 4.0, 6)
LLM_PROFILE = SimProfile("sim-llm-xl", 0.85, 8.0, 0.35, 0.22, 600.0, 25.0, 40)


class SimulatedModel:
    """Deterministic stand-in: correctness and confidence are hash-derived from (model, task)."""

    def __init__(self, profile: SimProfile):
        self.profile = profile
        self.name = profile.name

    def p_correct(self, difficulty: float) -> float:
        z = (self.profile.capability - difficulty) * self.profile.sharpness
        return 1.0 / (1.0 + math.exp(-z))

    def generate(self, task: Task) -> ModelOutput:
        p = self.profile
        correct = stable_unit(self.name, "correct", task.task_id) < self.p_correct(task.difficulty)
        answer = task.gold if correct else self._wrong_answer(task)
        noise = p.noise_sd * stable_normal(self.name, "conf", task.task_id)
        confidence = min(max(0.5 + p.calibration * (1 if correct else -1) + noise, 0.01), 0.99)
        out_tokens = estimate_tokens(answer) + p.overhead_output_tokens
        return ModelOutput(
            answer=answer,
            confidence=confidence,
            input_tokens=estimate_tokens(task.prompt) + 40,
            output_tokens=out_tokens,
            latency_ms=p.base_latency_ms + p.ms_per_output_token * out_tokens,
        )

    def _wrong_answer(self, task: Task) -> str:
        u = stable_unit(self.name, "wrong", task.task_id)
        if task.kind == "classify":
            options = [c for c in task.choices if c != task.gold]
            return options[int(u * len(options))]
        offset = int(u * 9) + 1  # 1..9, never 0
        return str(int(task.gold) + (offset if u < 0.5 else -offset))


class CachedModel:
    """Memoise per task so threshold sweeps reuse (possibly expensive) model calls."""

    def __init__(self, inner: Model):
        self.inner, self.name = inner, inner.name
        self._cache: dict[str, ModelOutput] = {}

    def generate(self, task: Task) -> ModelOutput:
        if task.task_id not in self._cache:
            self._cache[task.task_id] = self.inner.generate(task)
        return self._cache[task.task_id]


_ANSWER = re.compile(r"ANSWER:\s*(.+?)\s*(?:CONFIDENCE:|$)", re.IGNORECASE | re.DOTALL)
_CONF = re.compile(r"CONFIDENCE:\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)


class ParsedReply(NamedTuple):
    answer: str
    confidence: float
    well_formed: bool  # both ANSWER: and CONFIDENCE: were present


def parse_reply(text: str) -> ParsedReply:
    """Extract the answer and verbalised confidence from 'ANSWER: x CONFIDENCE: 0.8'.

    A missing confidence becomes a neutral 0.5 and the reply is flagged as not well formed.
    """
    m, c = _ANSWER.search(text), _CONF.search(text)
    answer = (m.group(1) if m else text).strip().strip(".").lower()
    conf = float(c.group(1)) if c else 0.5
    return ParsedReply(answer, min(max(conf, 0.0), 1.0), m is not None and c is not None)


def normalise_answer(task: Task, raw: str) -> str:
    """Pull the label/integer out of a chatty answer so grading is about content, not format."""
    if task.kind == "arith":
        numbers = re.findall(r"-?\d+", raw.replace(",", ""))
        return numbers[0] if numbers else raw
    low = raw.lower()
    found = [(low.find(c), c) for c in task.choices if c in low]
    return min(found)[1] if found else raw


def preflight(client, names: list[str], *, warmup: bool = True) -> dict:
    """Verify the server is reachable and each model is installed; warm models up.

    Warm-up loads each model into memory so the first measured request is not dominated by load
    time. Returns versions/digests for the results file. Raises LLMError with the fix.
    """
    try:
        version, installed = client.version(), client.list_models()
    except LLMError as exc:
        raise LLMError(
            f"cannot reach Ollama at {client.base_url}: {exc}. "
            "Install it from https://ollama.com and start it with `ollama serve`."
        ) from exc
    details = {}
    for name in dict.fromkeys(names):
        hit = next((m for m in installed if model_matches(m.get("name", ""), name)), None)
        if hit is None:
            raise LLMError(f"model {name!r} is not installed. Run: ollama pull {name}")
        d = hit.get("details", {})
        details[name] = {
            "digest": str(hit.get("digest", ""))[:12],
            "parameter_size": d.get("parameter_size"),
            "quantization": d.get("quantization_level"),
        }
        if warmup:
            client.generate("Reply with OK.", model=name, max_tokens=4, seed=0)
    return {"ollama_version": version, "models": details}


class OllamaModel:
    """A real local model via Ollama, using verbalised confidence (weakly calibrated in practice).

    Deterministic settings (temperature 0, fixed seed). Replies missing the requested format are
    still graded on whatever answer can be extracted, and counted in `malformed_rate`.
    """

    SUFFIX = (
        "\nReply in exactly this format: ANSWER: <your answer> CONFIDENCE: <number from 0 to 1>"
    )

    def __init__(self, client, model: str, *, max_tokens: int = 64, seed: int = 0):
        self.client, self.name = client, model
        self.max_tokens, self.seed = max_tokens, seed
        self.calls = self.malformed = 0

    @property
    def malformed_rate(self) -> float:
        return self.malformed / self.calls if self.calls else 0.0

    def generate(self, task: Task) -> ModelOutput:
        r = self.client.generate(
            task.prompt + self.SUFFIX,
            model=self.name,
            temperature=0.0,
            max_tokens=self.max_tokens,
            seed=self.seed,
        )
        parsed = parse_reply(r.text)
        self.calls += 1
        self.malformed += not parsed.well_formed
        answer = normalise_answer(task, parsed.answer)
        return ModelOutput(answer, parsed.confidence, r.input_tokens, r.output_tokens, r.latency_ms)
