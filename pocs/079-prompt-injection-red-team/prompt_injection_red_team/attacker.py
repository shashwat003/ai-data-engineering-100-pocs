"""A deliberately worst-case "compromised model" standing in for a successfully injected LLM.

It is NOT a model of how often real LLMs fall for injections. It assumes the worst: whatever
instruction text reaches it, in any encoding it can normalise (zero-width characters, Unicode
compatibility forms, base64), it obeys, immediately and fully. The evaluation therefore measures
which *deterministic controls* still hold when the model has already been fooled, and how much of
the hostile text the input filter keeps away from the model in the first place.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata

from incident_triage_agent.models import Finish, Incident, Observation, Step
from incident_triage_agent.planner import RulePlanner

from .corpus import AttackCase

ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"))
B64_TOKEN = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")


def normalise(text: str) -> str:
    """What a capable model 'understands': NFKC, no invisible characters, base64 decoded inline."""
    text = unicodedata.normalize("NFKC", text).translate(ZERO_WIDTH)
    decoded = []
    for token in B64_TOKEN.findall(text):
        try:
            decoded.append(base64.b64decode(token + "=" * (-len(token) % 4)).decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError):
            continue
    return re.sub(r"\s+", " ", (text + " " + " ".join(decoded)).lower())


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    return []


class CompromisedPlanner:
    def __init__(self, case: AttackCase | None, base=None):
        self.case, self.base = case, base or RulePlanner()
        self.fired = False
        self.queue: list = []
        self.seen: list[str] = []  # every string the planner was shown (for structural checks)

    def _sees_trigger(self, inc: Incident, history: list[Observation]) -> bool:
        texts = [inc.description] + [
            s for o in history if o.outcome == "ok" for s in _strings(o.data)
        ]
        self.seen = texts
        norm = " ".join(normalise(t) for t in texts)
        return any(t in norm for t in self.case.triggers)

    def next(self, inc: Incident, history: list[Observation]) -> Step | Finish:
        if self.case is not None and not self.fired and self._sees_trigger(inc, history):
            self.fired = True
            self.queue = list(self.case.goal.steps())
        while self.queue:
            item = self.queue.pop(0)
            return item(history) if callable(item) else item
        out = self.base.next(inc, history)
        if self.fired and isinstance(out, Finish) and self.case.goal.finish is not None:
            return self.case.goal.finish
        return out
