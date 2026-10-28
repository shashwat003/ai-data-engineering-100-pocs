"""Synthetic answers with exactly-known faithfulness, built from the shared knowledge base."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from data_gen import KnowledgeBase

from .guard import passages

CORRUPTIONS = (
    "wrong_number",
    "wrong_entity",
    "invented_claim",
    "fake_citation",
    "wrong_doc_citation",
    "uncited",
)
INVENTED = ["All data is replicated to three additional regions every hour.", "Access reviews are waived for executive accounts.",
            "The retention period can be changed by any employee without approval."]  # fmt: skip


@dataclass(frozen=True)
class Sentence:
    text: str
    faithful: bool
    kind: str  # faithful | paraphrase | one of CORRUPTIONS


def build(kb: KnowledgeBase, n: int, seed: int = 3) -> list[Sentence]:
    rng = random.Random(seed)
    store = passages(kb.docs)
    keys = [k for k, s in store.items() if len(s.split()) >= 6]
    systems = sorted({w for d in kb.docs for w in d.title.split() if w[0].isupper()})
    out: list[Sentence] = []
    for _i in range(n):
        key = rng.choice(keys)
        doc, idx = key
        s = store[key].rstrip(".")
        cite = f"[{doc}#{idx}]"
        kind = rng.choice(["faithful", "paraphrase", *CORRUPTIONS, *CORRUPTIONS])
        if kind == "faithful":
            out.append(Sentence(f"{s}. {cite}", True, kind))
        elif kind == "paraphrase":  # reworded but faithful: drop stopwords and reorder clauses
            parts = [p.strip() for p in re.split(r",| and ", s) if p.strip()]
            rng.shuffle(parts)
            out.append(Sentence(f"{' and '.join(parts)}. {cite}", True, kind))
        elif kind == "wrong_number":
            nums = re.findall(r"\d+", s)
            if not nums:
                out.append(Sentence(f"{s}. {cite}", True, "faithful"))
                continue
            n0 = rng.choice(nums)
            out.append(Sentence(f"{s.replace(n0, str(int(n0) + 7), 1)}. {cite}", False, kind))
        elif kind == "wrong_entity":
            ents = [w for w in re.findall(r"\b[A-Z][a-z]+\b", s) if w in systems]
            if not ents:
                out.append(Sentence(f"{s}. {cite}", True, "faithful"))
                continue
            other = rng.choice([x for x in systems if x != ents[0]])
            out.append(Sentence(f"{s.replace(ents[0], other)}. {cite}", False, kind))
        elif kind == "invented_claim":
            out.append(Sentence(f"{rng.choice(INVENTED)} {cite}", False, kind))
        elif kind == "fake_citation":
            out.append(Sentence(f"{s}. [doc-ghost#0]", False, kind))
        elif kind == "wrong_doc_citation":
            other = rng.choice([k for k in keys if k[0] != doc])
            out.append(Sentence(f"{s}. [{other[0]}#{other[1]}]", False, kind))
        else:
            out.append(Sentence(f"{s}.", False, "uncited"))
    return out
