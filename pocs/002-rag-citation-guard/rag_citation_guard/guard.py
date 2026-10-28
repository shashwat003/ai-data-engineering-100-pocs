"""Post-generation citation guard: every claim must cite a real passage that actually supports it.

Checks per sentence: (1) a citation is present and resolves to a retrieved passage, (2) the claim's
numbers and capitalised entities appear in the cited passage, (3) enough of its content words
appear there. Failing sentences are removed or the whole answer is blocked. A deterministic lexical
verifier: cheap and explainable, but it cannot detect a wrong claim built from supported words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CITE = re.compile(r"\[([A-Za-z0-9_.\-]+)#(\d+)\]")
WORD = re.compile(r"[A-Za-z0-9\-]+")
STOP = frozenset(
    "a an and are as at be by for from in is it of on or the to was with this that these those its their must should".split()
)


@dataclass(frozen=True)
class Verdict:
    sentence: str
    ok: bool
    reason: str
    overlap: float = 0.0


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?\]])\s+(?=[A-Z])", text.strip()) if s.strip()]


def passages(docs) -> dict[tuple[str, int], str]:
    out = {}
    for d in docs:
        for i, s in enumerate(split_sentences(d.text)):
            out[(d.doc_id, i)] = s
    return out


def _content(s: str) -> list[str]:
    return [w.lower() for w in WORD.findall(s) if w.lower() not in STOP]


def _entities(s: str) -> set[str]:
    words = WORD.findall(s)
    return {w for i, w in enumerate(words) if w[0].isupper() and i > 0 and not w.isdigit()} | set(
        re.findall(r"\b[A-Z]{2,}-\d+\b", s)
    )


def check_sentence(
    sentence: str, store: dict[tuple[str, int], str], threshold: float = 0.6
) -> Verdict:
    cites = CITE.findall(sentence)
    claim = CITE.sub("", sentence).strip()
    if not cites:
        return Verdict(sentence, False, "no citation")
    cited = []
    for doc, idx in cites:
        p = store.get((doc, int(idx)))
        if p is None:
            return Verdict(sentence, False, f"citation {doc}#{idx} does not exist")
        cited.append(p)
    ctx = " ".join(cited)
    nums = set(re.findall(r"\d+(?:\.\d+)?", claim))
    if not nums <= set(re.findall(r"\d+(?:\.\d+)?", ctx)):
        return Verdict(
            sentence,
            False,
            f"number(s) {sorted(nums - set(re.findall(r'[0-9.]+', ctx)))} not in cited passage",
        )
    missing = {e for e in _entities(claim) if e.lower() not in ctx.lower()}
    if missing:
        return Verdict(sentence, False, f"entity {sorted(missing)} not in cited passage")
    words = _content(claim)
    ctx_words = set(_content(ctx))
    overlap = sum(w in ctx_words for w in words) / len(words) if words else 1.0
    if overlap < threshold:
        return Verdict(sentence, False, f"only {overlap:.0%} of content words supported", overlap)
    return Verdict(sentence, True, "supported", overlap)


def guard(
    answer: str, store: dict, *, threshold: float = 0.6, block_if_unsupported_share: float = 0.5
) -> dict:
    verdicts = [check_sentence(s, store, threshold) for s in split_sentences(answer)]
    bad = [v for v in verdicts if not v.ok]
    share = len(bad) / len(verdicts) if verdicts else 1.0
    blocked = share > block_if_unsupported_share or not verdicts
    kept = " ".join(v.sentence for v in verdicts if v.ok)
    return {
        "blocked": blocked,
        "text": "" if blocked else kept,
        "verdicts": verdicts,
        "unsupported_share": round(share, 3),
    }
