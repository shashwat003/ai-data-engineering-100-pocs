"""A small trainable tagger for person names and street addresses (logistic regression on token features)."""

from __future__ import annotations

import re

import numpy as np
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression

TOKEN = re.compile(r"\w+|[^\w\s]")
STREET_WORDS = {"street", "avenue", "road", "lane", "drive", "court", "st", "ave", "rd"}
LABELS = ["O", "PERSON", "ADDRESS"]


def tokenize(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group()) for m in TOKEN.finditer(text)]


def shape(w: str) -> str:
    s = re.sub(r"[A-Z]", "X", w)
    s = re.sub(r"[a-z]", "x", s)
    return re.sub(r"\d", "d", s)[:6]


def features(toks: list[tuple[int, int, str]], i: int, identity: bool = True) -> dict:
    w = toks[i][2]
    prev = toks[i - 1][2] if i else "<s>"
    nxt = toks[i + 1][2] if i + 1 < len(toks) else "</s>"
    prev2 = toks[i - 2][2] if i > 1 else "<s>"
    next2 = toks[i + 2][2] if i + 2 < len(toks) else "</s>"
    f = {
        "shape": shape(w), "cap": w[:1].isupper(), "digit": w.isdigit(), "first": i == 0 or prev in {".", "!", "?"},
        "prev_lower": prev.lower(), "next_lower": nxt.lower(), "prev_cap": prev[:1].isupper(), "next_cap": nxt[:1].isupper(),
        "prev2_lower": prev2.lower(), "next2_lower": next2.lower() if not next2[:1].isupper() else "<cap>", "next_street": nxt.lower() in STREET_WORDS, "prev_street": prev.lower() in STREET_WORDS,
        "prev_digit": prev.isdigit(), "is_street": w.lower() in STREET_WORDS,
    }  # fmt: skip
    if identity:  # token identity / affix features: strong on seen names, memorise them
        f.update(
            {
                "lower": w.lower(),
                "suf2": w[-2:].lower(),
                "suf3": w[-3:].lower(),
                "pre2": w[:2].lower(),
            }
        )
    elif not w[
        :1
    ].isupper():  # context-only mode still lets lowercase function words speak for themselves
        f["lower"] = w.lower()
    return f


def _labels(toks, spans) -> list[str]:
    out = []
    for s, e, _ in toks:
        lab = "O"
        for a, b, tag in spans:
            if tag in ("PERSON", "ADDRESS") and s >= a and e <= b:
                lab = tag
        out.append(lab)
    return out


class Tagger:
    def __init__(self, c: float = 2.0, identity: bool = True):
        self.identity = identity
        self.vec = DictVectorizer()
        self.clf = LogisticRegression(C=c, max_iter=400)

    def fit(self, data) -> Tagger:
        X, y = [], []
        for text, spans in data:
            toks = tokenize(text)
            lab = _labels(toks, spans)
            X += [features(toks, i, self.identity) for i in range(len(toks))]
            y += lab
        self.clf.fit(self.vec.fit_transform(X), y)
        return self

    def predict_spans(self, text: str, min_prob: float = 0.5) -> list[tuple[int, int, str]]:
        toks = tokenize(text)
        if not toks:
            return []
        proba = self.clf.predict_proba(
            self.vec.transform([features(toks, i, self.identity) for i in range(len(toks))])
        )
        classes = list(self.clf.classes_)
        spans, cur = [], None
        for i, (s, e, _) in enumerate(toks):
            k = int(np.argmax(proba[i]))
            lab = classes[k] if proba[i][k] >= min_prob else "O"
            if lab == "O":
                cur = None
                continue
            if cur and cur[2] == lab and s - cur[1] <= 1:
                cur[1] = e
            else:
                cur = [s, e, lab]
                spans.append(cur)
        return [tuple(sp) for sp in spans]
