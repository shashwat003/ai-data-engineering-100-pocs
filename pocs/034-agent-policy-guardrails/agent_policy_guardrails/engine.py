"""Rule engine: load YAML policy, evaluate text at a stage, redact, rate-limit, and lint the rules."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .detectors import DETECTORS

SEVERITY = {"allow": 0, "redact": 1, "escalate": 2, "block": 3}
DEFAULT_POLICY = Path(__file__).with_name("rules.yaml")


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    id: str
    stage: tuple[str, ...]
    detector: str
    action: str
    replacement: str = "[REDACTED]"
    params: dict = field(default_factory=dict)


@dataclass
class Decision:
    action: str  # allow | redact | escalate | block
    text: str  # text after redaction ("" when blocked)
    findings: list[tuple[str, str]]  # (rule id, label) only; matched content is never stored
    reason: str = ""


def load_policy(path: str | Path = DEFAULT_POLICY) -> tuple[list[Rule], dict]:
    doc = yaml.safe_load(Path(path).read_text())
    rules, seen = [], set()
    for r in doc.get("rules", []):
        if r["id"] in seen:
            raise PolicyError(f"duplicate rule id {r['id']}")
        seen.add(r["id"])
        if r["detector"] not in DETECTORS:
            raise PolicyError(f"{r['id']}: unknown detector {r['detector']}")
        if r["action"] not in SEVERITY:
            raise PolicyError(f"{r['id']}: unknown action {r['action']}")
        stage = tuple(r["stage"])
        if not stage or not set(stage) <= {"input", "output"}:
            raise PolicyError(f"{r['id']}: bad stage {stage}")
        rules.append(
            Rule(
                r["id"],
                stage,
                r["detector"],
                r["action"],
                r.get("replacement", "[REDACTED]"),
                r.get("params", {}),
            )
        )
    return rules, doc.get("limits", {})


def lint(rules: list[Rule]) -> list[str]:
    """Static checks: two rules on the same detector and stage where one is weaker are shadowed."""
    msgs = []
    for i, a in enumerate(rules):
        for b in rules[i + 1 :]:
            if a.detector == b.detector and set(a.stage) & set(b.stage):
                msgs.append(
                    f"{b.id} overlaps {a.id} (same detector, shared stage): the {'stricter' if SEVERITY[a.action] >= SEVERITY[b.action] else 'weaker'} action decides"
                )
    return msgs


def apply_redactions(text: str, spans: list[tuple[int, int, str]]) -> str:
    out, pos = [], 0
    for s, e, rep in sorted(spans):
        if s < pos:  # overlapping spans: skip, the earlier one already covers it
            continue
        out.append(text[pos:s])
        out.append(rep)
        pos = e
    out.append(text[pos:])
    return "".join(out)


class Guard:
    def __init__(
        self,
        rules: list[Rule] | None = None,
        limits: dict | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        if rules is None:
            rules, limits = load_policy()
        self.rules, self.limits, self.clock = rules, limits or {}, clock
        self._req: dict[str, deque] = defaultdict(deque)
        self._tok: dict[str, deque] = defaultdict(deque)

    def check(self, text: str, stage: str) -> Decision:
        action, findings, spans = "allow", [], []
        for r in self.rules:
            if stage not in r.stage:
                continue
            hits = DETECTORS[r.detector](text, **r.params)
            if not hits:
                continue
            findings += [(r.id, label) for _, _, label in hits[:1]]
            if SEVERITY[r.action] > SEVERITY[action]:
                action = r.action
            if r.action == "redact":
                spans += [(s, e, r.replacement) for s, e, _ in hits]
        if action in ("block", "escalate"):
            return Decision(
                action, "", findings, f"{action} by {', '.join(sorted({f[0] for f in findings}))}"
            )
        return Decision(
            action, apply_redactions(text, spans) if action == "redact" else text, findings
        )

    def admit(self, user: str, tokens: int) -> tuple[bool, str]:
        """Sliding-window rate limits per user: requests per minute and tokens per hour."""
        now = self.clock()
        rq, tk = self._req[user], self._tok[user]
        while rq and now - rq[0] > 60:
            rq.popleft()
        while tk and now - tk[0][0] > 3600:
            tk.popleft()
        if len(rq) >= self.limits.get("requests_per_minute", 10**9):
            return False, "rate limit: requests per minute"
        if sum(t for _, t in tk) + tokens > self.limits.get("tokens_per_hour", 10**12):
            return False, "budget: tokens per hour"
        rq.append(now)
        tk.append((now, tokens))
        return True, "ok"
