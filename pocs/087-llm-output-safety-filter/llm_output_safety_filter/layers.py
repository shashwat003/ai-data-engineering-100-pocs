"""Output safety layers. Each returns findings (layer, label, severity, span or None)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from agent_policy_guardrails.detectors import DETECTORS
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

from .normalise import normalise

ALLOWED_DOMAINS = {"docs.acme.example", "www.acme.example", "acme.example"}
URL = re.compile(r"(?i)\bhttps?://[^\s)<>\"']+")
IMG = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
IP_HOST = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
DANGEROUS_CMD = [
    ("pipe-to-shell", re.compile(r"(?i)\b(?:curl|wget)\b[^|\n]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b")),
    (
        "recursive-root-delete",
        re.compile(r"(?i)\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+(?:--no-preserve-root\s+)?/(?:\s|$)"),
    ),
    ("world-writable-root", re.compile(r"(?i)\bchmod\s+-R\s+777\s+/(?:\s|$)")),
    ("fork-bomb", re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:")),
]


@dataclass(frozen=True)
class Finding:
    layer: str
    label: str
    severity: str  # "block" | "redact" | "escalate"
    span: tuple[int, int] | None = None
    score: float | None = None


def pii_secrets(text: str, **_) -> list[Finding]:
    out = []
    for name, label in (
        ("credit_card", "credit_card"),
        ("us_ssn", "ssn"),
        ("email", "email"),
        ("phone", "phone"),
        ("iban", "iban"),
        ("secret_token", "secret"),
    ):
        out += [
            Finding("pii_secrets", label, "redact", (s, e)) for s, e, _ in DETECTORS[name](text)
        ]
    return out


def links(text: str, **_) -> list[Finding]:
    out = []
    for m in IMG.finditer(
        text
    ):  # a markdown image to an external host is a classic exfiltration channel
        host = urlparse(m.group(1)).hostname or ""
        if host not in ALLOWED_DOMAINS:
            out.append(Finding("links", "external_image", "block", m.span()))
    for m in URL.finditer(text):
        host = (urlparse(m.group().rstrip(".,;")).hostname or "").lower()
        if host in ALLOWED_DOMAINS:
            continue
        out.append(
            Finding(
                "links",
                "ip_url" if IP_HOST.match(host) else "unlisted_domain",
                "block" if IP_HOST.match(host) or host.endswith(".exe") else "redact",
                m.span(),
            )
        )
    return out


def commands(text: str, **_) -> list[Finding]:
    return [
        Finding("commands", name, "block", m.span())
        for name, rx in DANGEROUS_CMD
        for m in rx.finditer(text)
    ]


def _shingles(s: str, n: int = 4) -> set[tuple[str, ...]]:
    w = re.findall(r"[a-z0-9']+", s.lower())
    return {tuple(w[i : i + n]) for i in range(len(w) - n + 1)}


def prompt_leak(text: str, system_prompt: str = "", threshold: float = 0.25, **_) -> list[Finding]:
    ref = _shingles(system_prompt)
    if not ref:
        return []
    overlap = len(ref & _shingles(text)) / len(ref)
    return (
        [Finding("prompt_leak", "system_prompt_overlap", "block", None, round(overlap, 2))]
        if overlap >= threshold
        else []
    )


def ungrounded_numbers(text: str, context: str | None = None, **_) -> list[Finding]:
    if context is None:
        return []
    num = re.compile(r"\d[\d,]*\.?\d*")
    allowed = {x.replace(",", "").rstrip(".") for x in num.findall(context)}
    bad = [
        m
        for m in num.finditer(text)
        if m.group().replace(",", "").rstrip(".") not in allowed
        and len(m.group().replace(",", "")) >= 2
    ]
    # derived percentages are not checked here: an unmatched number is escalated, not blocked
    return [Finding("grounding", "ungrounded_number", "escalate", m.span()) for m in bad]


class TextClassifier:
    """TF-IDF + logistic regression over word and character n-grams, trained on synthetic data."""

    def __init__(self, c: float = 8.0):
        from sklearn.pipeline import FeatureUnion

        feats = FeatureUnion(
            [
                ("w", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)),
                ("c", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)),
            ]
        )
        self.model = make_pipeline(
            feats, LogisticRegression(C=c, max_iter=800, class_weight="balanced")
        )

    def fit(self, texts, labels):
        self.model.fit(texts, labels)
        return self

    def predict(self, text: str) -> tuple[str, float]:
        p = self.model.predict_proba([text])[0]
        k = p.argmax()
        return self.model.classes_[k], float(p[k])


def make_classifier_layer(clf: TextClassifier, block_at: float = 0.7, escalate_at: float = 0.45):
    def layer(text: str, **_) -> list[Finding]:
        label, p = clf.predict(text)
        if label == "benign":
            return []
        if p >= block_at:
            return [Finding("classifier", label, "block", None, round(p, 2))]
        if p >= escalate_at:
            return [Finding("classifier", label, "escalate", None, round(p, 2))]
        return []

    return layer


def normalised(layer):
    """Run a layer on canonicalised text. Spans refer to the canonical text, so redaction uses the original text instead."""

    def run(text: str, **kw):
        return layer(normalise(text), **kw)

    return run
