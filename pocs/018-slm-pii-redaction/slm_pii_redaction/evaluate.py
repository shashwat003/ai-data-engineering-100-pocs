from __future__ import annotations

import time
from collections import defaultdict

from .data import dataset
from .redact import detect
from .tagger import Tagger

TYPES = ["PERSON", "ADDRESS", "EMAIL", "PHONE", "CREDIT_CARD", "IBAN", "US_SSN"]


def score(data, tagger) -> dict:
    tp, fp, fn = defaultdict(int), defaultdict(int), defaultdict(int)
    leaked = total_pii = over = non_pii = 0
    for text, truth in data:
        pred = detect(text, tagger)
        t_set, p_set = {(s, e, lab) for s, e, lab in truth}, set(pred)
        for lab in TYPES:
            tl, pl = {x for x in t_set if x[2] == lab}, {x for x in p_set if x[2] == lab}
            tp[lab] += len(tl & pl)
            fp[lab] += len(pl - tl)
            fn[lab] += len(tl - pl)
        covered = [False] * len(text)
        for s, e, _ in pred:
            for i in range(s, e):
                covered[i] = True
        pii_chars = [False] * len(text)
        for s, e, _ in truth:
            for i in range(s, e):
                pii_chars[i] = True
        leaked += sum(
            1 for i, p in enumerate(pii_chars) if p and not covered[i] and text[i].strip()
        )
        total_pii += sum(1 for i, p in enumerate(pii_chars) if p and text[i].strip())
        over += sum(1 for i, c in enumerate(covered) if c and not pii_chars[i] and text[i].strip())
        non_pii += sum(1 for i, p in enumerate(pii_chars) if not p and text[i].strip())
    per = {}
    for lab in TYPES:
        p = tp[lab] / max(1, tp[lab] + fp[lab])
        r = tp[lab] / max(1, tp[lab] + fn[lab])
        per[lab] = {
            "precision": round(p, 3),
            "recall": round(r, 3),
            "f1": round(2 * p * r / max(1e-9, p + r), 3),
            "support": tp[lab] + fn[lab],
        }
    return {
        "per_type": per,
        "char_leak_rate": round(leaked / max(1, total_pii), 4),
        "over_redaction_rate": round(over / max(1, non_pii), 4),
    }


def run(n_train: int = 1500, n_test: int = 400) -> dict:
    train, test = dataset(n_train, 1, False), dataset(n_test, 2, True)
    tagger = Tagger(identity=False).fit(train)
    memo = Tagger(identity=True).fit(train)
    seen = dataset(n_test, 3, False)
    new_tpl = dataset(n_test, 4, True, held_out=True)
    res = {"unseen_names": score(test, tagger), "unseen_names_and_templates": score(new_tpl, tagger), "identity_new_templates": score(new_tpl, memo), "seen_names": score(seen, tagger), "patterns_only": score(test, None),
           "identity_features": {"unseen_names": score(test, memo), "seen_names": score(seen, memo)}}  # fmt: skip
    # naive baseline: every capitalised word that is not sentence-initial is a name
    t0 = time.perf_counter()
    for text, _ in test:
        detect(text, tagger)
    res["texts_per_second"] = round(len(test) / (time.perf_counter() - t0), 1)
    return res
