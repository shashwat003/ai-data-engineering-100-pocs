"""Layered filter: cheap deterministic layers first, classifier last; block > escalate > redact > allow."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .layers import (
    Finding,
    commands,
    links,
    make_classifier_layer,
    pii_secrets,
    prompt_leak,
    ungrounded_numbers,
)
from .normalise import normalise

ORDER = ["pii_secrets", "links", "commands", "prompt_leak", "grounding", "classifier"]
REFUSAL = "I can't share that response."
HOLD = "This response needs human review before it can be shown."


@dataclass
class Result:
    decision: str  # allow | redact | escalate | block
    text: str
    findings: list[Finding] = field(default_factory=list)
    ms: float = 0.0


class Pipeline:
    def __init__(
        self,
        classifier=None,
        system_prompt: str = "",
        enabled: set[str] | None = None,
        normalise_input: bool = True,
    ):
        self.layers = {
            "pii_secrets": pii_secrets,
            "links": links,
            "commands": commands,
            "prompt_leak": prompt_leak,
            "grounding": ungrounded_numbers,
        }
        if classifier is not None:
            self.layers["classifier"] = make_classifier_layer(classifier)
        self.enabled = (
            set(self.layers) if enabled is None else {e for e in enabled if e in self.layers}
        )
        self.system_prompt, self.normalise_input = system_prompt, normalise_input

    def run(self, text: str, context: str | None = None) -> Result:
        t0 = time.perf_counter()
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        text = text[:20_000]  # bound work; anything longer is truncated before scanning
        canon = normalise(text) if self.normalise_input else text
        kw = {"system_prompt": self.system_prompt, "context": context}
        findings: list[Finding] = []
        for name in ORDER:
            if name not in self.enabled:
                continue
            orig = self.layers[name](text, **kw)
            findings += orig
            if canon != text:
                # The canonical copy is only used for matching, never shown. A redactable item found there cannot be cut out of
                # the original reliably (a partial raw match would leave part of it behind), so it blocks the output.
                seen = {f.label for f in orig}
                orig_items = {text[f.span[0] : f.span[1]] for f in orig if f.span}
                for f in self.layers[name](canon, **kw):
                    if f.severity == "redact":
                        if f.span and canon[f.span[0] : f.span[1]] in orig_items:
                            continue  # the same item the raw text already yielded; it can be redacted normally
                        findings.append(Finding(f.layer, f.label, "block", None, f.score))
                    elif f.label not in seen:
                        findings.append(Finding(f.layer, f.label, f.severity, None, f.score))
            if any(f.severity == "block" for f in findings):
                break  # short-circuit: no need to run costlier layers
        sev = {f.severity for f in findings}
        if "block" in sev:
            res = Result("block", REFUSAL, findings)
        elif "escalate" in sev:
            res = Result("escalate", HOLD, findings)
        elif "redact" in sev:
            spans = sorted({f.span for f in findings if f.severity == "redact" and f.span})
            out, last = [], 0
            for s, e in spans:
                if s >= last:
                    out += [text[last:s], "[REDACTED]"]
                    last = e
            res = Result("redact", "".join(out) + text[last:], findings)
        else:
            res = Result("allow", text, findings)
        res.ms = (time.perf_counter() - t0) * 1000
        return res
