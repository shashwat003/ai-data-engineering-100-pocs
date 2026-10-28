from __future__ import annotations

import random
import statistics

from . import data
from .layers import TextClassifier
from .pipeline import ORDER, Pipeline

EVASIONS = {
    "leetspeak": data.leet,
    "look-alike letters": data.homoglyph,
    "spaced letters": data.spaced,
    "zero-width chars": data.zero_width,
}


def train_classifier(seed: int = 1) -> TextClassifier:
    texts, labels = data.classifier_data(random.Random(seed), "train")
    return TextClassifier().fit(texts, labels)


def classifier_metrics(clf: TextClassifier, seed: int = 2) -> dict:
    texts, labels = data.classifier_data(random.Random(seed), "test")
    pred = [clf.predict(t)[0] for t in texts]
    acc = sum(p == y for p, y in zip(pred, labels, strict=True)) / len(labels)
    per = {}
    for c in sorted(set(labels)):
        tp = sum(p == c and y == c for p, y in zip(pred, labels, strict=True))
        per[c] = {
            "recall": round(tp / max(1, labels.count(c)), 3),
            "precision": round(tp / max(1, pred.count(c)), 3),
        }
    return {"accuracy": round(acc, 3), "per_class": per, "n": len(labels)}


def cases(seed: int = 5) -> dict:
    rng = random.Random(seed)
    tox = [t.format(w=w) for t in data.TOX_TEST for w in data.W_TEST[:3]]
    unsafe = {
        "toxic": tox, "self_harm": [t.format(w="alone") for t in data.SH_TEST], "weapons": list(data.WEAP_TEST), "dangerous_command": list(data.CMD_BAD),
        "prompt_leak": list(data.LEAK), "bad_link": list(data.LINK_BAD), "pii": data.pii_samples(rng),
    }  # fmt: skip
    benign = list(data.BENIGN_TEST) + data.LINK_OK + data.CMD_OK + data.HALLU_OK
    return {"unsafe": unsafe, "benign": benign, "hallucinated": data.HALLU_BAD}


def caught(p: Pipeline, text: str, ctx=None) -> bool:
    return p.run(text, ctx).decision != "allow"


def run() -> dict:
    clf = train_classifier()
    full = Pipeline(clf, data.SYSTEM_PROMPT)
    no_norm = Pipeline(clf, data.SYSTEM_PROMPT, normalise_input=False)
    c = cases()
    out: dict = {"classifier": classifier_metrics(clf)}
    out["recall_clean"] = {
        k: round(sum(caught(full, t) for t in v) / len(v), 3) for k, v in c["unsafe"].items()
    }
    out["recall_clean"]["ungrounded_numbers"] = round(
        sum(caught(full, t, data.HALLU_CONTEXT) for t in c["hallucinated"])
        / len(c["hallucinated"]),
        3,
    )
    out["benign_blocked_or_changed"] = round(
        sum(caught(full, t, data.HALLU_CONTEXT) for t in c["benign"]) / len(c["benign"]), 3
    )
    out["benign_false_positives"] = [t for t in c["benign"] if caught(full, t, data.HALLU_CONTEXT)]
    ev: dict = {}
    base = [
        t
        for k in ("toxic", "self_harm", "weapons", "dangerous_command", "prompt_leak")
        for t in c["unsafe"][k]
    ]
    for name, fn in EVASIONS.items():
        texts = [fn(t) for t in base]
        ev[name] = {
            "with_normaliser": round(sum(caught(full, t) for t in texts) / len(texts), 3),
            "without": round(sum(caught(no_norm, t) for t in texts) / len(texts), 3),
        }
    out["evasion"] = ev
    abl = {}
    pool = [t for v in c["unsafe"].values() for t in v]
    for layer in ORDER:
        p = Pipeline(clf, data.SYSTEM_PROMPT, enabled=set(ORDER) - {layer})
        abl[layer] = round(sum(caught(p, t) for t in pool) / len(pool), 3)
    out["recall_all_layers"] = round(sum(caught(full, t) for t in pool) / len(pool), 3)
    out["recall_without_layer"] = abl
    lat = sorted(full.run(t).ms for t in pool + c["benign"] for _ in range(3))
    out["latency_ms"] = {
        "p50": round(statistics.median(lat), 2),
        "p95": round(lat[int(0.95 * len(lat)) - 1], 2),
    }
    return out
