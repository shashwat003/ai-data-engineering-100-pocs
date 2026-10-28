"""Tool router (LoRA-adapted MLP from POC 015) + slot tagger (logistic regression) + typed decoder, and a keyword/regex baseline."""

from __future__ import annotations

import copy
import re

import numpy as np
from qlora_ticket_classifier.lora_net import LoraNet, featurize, fit, softmax
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression

from .data import AIRPORTS, CURRENCY_WORDS, MONTHS, NUMBER_WORDS, UNITS, generate
from .tools import TOOLS

CLASSES = [*sorted(TOOLS), "none"]
COARSE = {
    "get_weather": 0,
    "search_flights": 0,
    "convert_currency": 0,
    "set_timer": 1,
    "send_email": 1,
    "create_event": 1,
    "none": 2,
}
SIZES = [1024, 256, 128]
RANK, ALPHA = 8, 16.0
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EMAIL = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")
IATA = re.compile(r"^[A-Z]{3}$")


# ---------------------------------------------------------------- router
def pretrain_router(seed: int = 0, n: int = 3000, epochs: int = 12) -> LoraNet:
    ex = generate(n, "train", seed + 500)
    x, y = featurize([e["text"] for e in ex]), np.array([COARSE[e["tool"]] for e in ex])
    net = LoraNet(SIZES, 3, seed)
    net.mode = "full"
    fit(net, x, y, epochs=epochs, lr=3e-3, seed=seed)
    return net


def train_router(
    base: LoraNet | None, examples: list[dict], mode: str, seed: int, epochs: int = 40
) -> LoraNet:
    """mode: 'lora' (frozen pretrained base + adapters), 'scratch' (same architecture, random init, all weights trained)."""
    x, y = (
        featurize([e["text"] for e in examples]),
        np.array([CLASSES.index(e["tool"]) for e in examples]),
    )
    if mode == "scratch":
        m = LoraNet(SIZES, len(CLASSES), seed)
        m.mode = "full"
        fit(m, x, y, epochs=epochs, lr=3e-3, seed=seed)
        return m
    m = copy.deepcopy(base)
    m.reset_head(len(CLASSES), seed)
    m.add_lora(RANK, ALPHA, seed)
    m.mode = "lora"
    fit(m, x, y, epochs=epochs, lr=5e-3, seed=seed)
    return m


def route(net: LoraNet, texts: list[str]) -> tuple[list[str], np.ndarray]:
    p = softmax(net.forward(featurize(texts)))
    return [CLASSES[i] for i in p.argmax(1)], p.max(1)


# ---------------------------------------------------------------- slot tagger
def token_features(words: list[str], i: int) -> dict:
    w = words[i]
    f = {"w": w.lower(), "shape_cap": w[:1].isupper(), "digit": w.isdigit(), "upper3": bool(IATA.match(w)), "at": "@" in w, "iso": bool(ISO.match(w)),
         "month": w in MONTHS, "len": min(len(w), 12), "first": i == 0}  # fmt: skip
    for off in (-2, -1, 1, 2):
        j = i + off
        f[f"w{off}"] = words[j].lower() if 0 <= j < len(words) else "<pad>"
    return f


class Tagger:
    def __init__(self):
        self.vec = DictVectorizer()
        self.clf = LogisticRegression(max_iter=500, C=5)

    def fit(self, examples: list[dict]) -> Tagger:
        feats, tags = [], []
        for e in examples:
            words = e["text"].split()
            feats += [token_features(words, i) for i in range(len(words))]
            tags += e["tags"]
        self.clf.fit(self.vec.fit_transform(feats), tags)
        return self

    def spans(self, text: str) -> dict[str, str]:
        words = text.split()
        if not words:
            return {}
        pred = self.clf.predict(
            self.vec.transform([token_features(words, i) for i in range(len(words))])
        )
        out: dict[str, list[str]] = {}
        prev = "O"
        for w, t in zip(words, pred, strict=True):
            if t != "O":
                if (
                    t == prev or t not in out
                ):  # contiguous tokens of one slot; a second separate span is ignored
                    out.setdefault(t, []).append(w)
            prev = t
        return {k: " ".join(v) for k, v in out.items()}


# ---------------------------------------------------------------- typed decoding
def _to_int(s: str):
    if s.isdigit():
        return int(s)
    return NUMBER_WORDS.get(s.lower(), s)


def _to_airport(s: str):
    if IATA.match(s):
        return s
    return AIRPORTS.get(s.title(), s)


def _to_date(s: str):
    if ISO.match(s):
        return s
    m = re.match(r"^([A-Za-z]+) (\d{1,2}) (\d{4})$", s)
    if m and m.group(1).title() in MONTHS:
        return f"{m.group(3)}-{MONTHS.index(m.group(1).title()) + 1:02d}-{int(m.group(2)):02d}"
    return s


def _to_currency(s: str):
    return s if IATA.match(s) else CURRENCY_WORDS.get(s.lower(), s)


def _to_float(s: str):
    try:
        return float(s)
    except ValueError:
        return s


COERCE = {
    "minutes": _to_int,
    "origin": _to_airport,
    "destination": _to_airport,
    "date": _to_date,
    "from_currency": _to_currency,
    "to_currency": _to_currency,
    "amount": _to_float,
}


def assemble(tool: str, spans: dict[str, str]) -> dict:
    """Build the call from spans. Unparsable values are kept raw, so the schema gate (not this function) rejects them."""
    props = TOOLS[tool]["properties"]
    args = {}
    for k, v in spans.items():
        if k in props:
            args[k] = COERCE.get(k, lambda s: s)(v)
    if "unit" in args and str(args["unit"]).lower() in UNITS:
        args["unit"] = str(args["unit"]).lower()
    return {"name": tool, "arguments": args}


# ---------------------------------------------------------------- keyword/regex baseline (stands in for a prompted, untuned model)
KEYWORDS = {
    "get_weather": ["weather", "forecast", "raining", "temperature", "conditions"], "set_timer": ["timer", "minutes", "wake", "count", "ping"],
    "send_email": ["email", "write", "message", "note", "line"], "search_flights": ["flight", "flights", "fly", "seat"],
    "convert_currency": ["convert", "exchange", "worth", "change", "much"], "create_event": ["event", "calendar", "schedule", "remind", "block", "put"],
}  # fmt: skip


def baseline_route(text: str) -> tuple[str, float]:
    words = set(re.findall(r"[a-z]+", text.lower()))
    scores = {t: len(words & set(k)) for t, k in KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return (best, 1.0) if scores[best] > 0 else ("none", 1.0)


def baseline_spans(tool: str, text: str) -> dict[str, str]:
    """Regex slot filling: the obvious surface patterns, no learning."""
    out: dict[str, str] = {}
    words = text.split()
    emails = [w for w in words if EMAIL.match(w)]
    if emails and "to" in TOOLS.get(tool, {}).get("properties", {}):
        out["to"] = emails[0]
    iso = [w for w in words if ISO.match(w)]
    if iso:
        out["date"] = iso[0]
    codes = [w for w in words if IATA.match(w)]
    if tool == "search_flights" and len(codes) >= 2:
        out["origin"], out["destination"] = codes[0], codes[1]
    if tool == "convert_currency" and len(codes) >= 2:
        out["from_currency"], out["to_currency"] = codes[0], codes[1]
    nums = [w for w in words if re.match(r"^\d+(\.\d+)?$", w)]
    if nums and tool == "set_timer":
        out["minutes"] = nums[0]
    if nums and tool == "convert_currency":
        out["amount"] = nums[0]
    caps = [w for w in words[1:] if w[:1].isupper() and not IATA.match(w)]
    if caps and tool == "get_weather":
        out["city"] = caps[0]
    return out
