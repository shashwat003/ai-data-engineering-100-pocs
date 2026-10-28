"""Synthetic internal-policy knowledge base with labelled questions of four difficulty kinds.

Every document is one (system, policy area) pair, so documents in the same area share vocabulary
and the retriever must use the system name and area to tell them apart. Question kinds:

- keyword:    runbook-code lookup ("RB-1042 retention"); favours exact-match retrieval
- natural:    wording that reuses the document's vocabulary
- paraphrase: area word replaced by a synonym that does NOT occur in the gold document
- typo:       natural question with two content words misspelled
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass

SYSTEMS = (
    "Atlas Beacon Cobalt Driftwood Ember Falcon Granite Harbor Ion Juniper Kestrel Lumen "
    "Mosaic Nimbus Orchid Pioneer Quartz Rivet Sparrow Tundra Umbra Vector Willow Zephyr"
).split()


@dataclass(frozen=True)
class Attr:
    key: str
    values: tuple[str, ...]
    fact: str  # sentence template: {S} system, {v} value
    natural: str
    para: str  # uses {alt}: a synonym absent from the gold document


@dataclass(frozen=True)
class Area:
    terms: tuple[str, str, str]  # terms[0] is canonical and appears in every document
    attrs: tuple[Attr, Attr, Attr]


AREAS: dict[str, Area] = {
    "backup": Area(
        ("backup", "snapshot", "restore point"),
        (
            Attr(
                "retention",
                ("7 days", "14 days", "30 days", "60 days", "90 days", "180 days"),
                "{S} backup copies are retained for {v}.",
                "How long are {S} backups retained?",
                "For how long does {S} keep each {alt}?",
            ),
            Attr(
                "schedule",
                ("01:00 UTC", "02:30 UTC", "04:00 UTC", "23:15 UTC"),
                "The nightly {S} backup job starts at {v}.",
                "When does the {S} backup job start?",
                "At what time does the {S} {alt} run begin?",
            ),
            Attr(
                "vault",
                ("vault-eu", "vault-us", "vault-ap"),
                "Encrypted {S} backup data is written to {v}.",
                "Where is {S} backup data stored?",
                "Which storage target receives the {S} {alt} files?",
            ),
        ),
    ),
    "access": Area(
        ("access", "entitlement", "permission"),
        (
            Attr(
                "review",
                ("monthly", "quarterly", "every six months"),
                "{S} access rights are reviewed {v} by the owning team.",
                "How often are {S} access rights reviewed?",
                "What is the audit cadence for {S} {alt} grants?",
            ),
            Attr(
                "timeout",
                ("15 minutes", "20 minutes", "30 minutes", "45 minutes", "60 minutes"),
                "Interactive sessions on {S} expire after {v} of inactivity.",
                "After how long do {S} sessions expire?",
                "How long can an idle {S} login stay open?",
            ),
            Attr(
                "approver",
                (
                    "Platform Security",
                    "Data Governance",
                    "Infrastructure Operations",
                    "Application Support",
                ),
                "Requests for {S} access are approved by {v}.",
                "Who approves {S} access requests?",
                "Which team signs off on {S} {alt} changes?",
            ),
        ),
    ),
    "deployment": Area(
        ("deployment", "release", "rollout"),
        (
            Attr(
                "freeze",
                (
                    "Fridays after 14:00",
                    "weekends",
                    "the last two days of each month",
                    "Thursdays after 16:00",
                ),
                "The {S} deployment freeze window covers {v}.",
                "When is the {S} deployment freeze window?",
                "Which period is blocked for a {alt} to {S}?",
            ),
            Attr(
                "rollback",
                ("5 minutes", "10 minutes", "15 minutes", "30 minutes"),
                "A failed {S} deployment must be rolled back within {v}.",
                "Within how long must a failed {S} deployment be rolled back?",
                "What is the maximum time to revert a bad {S} {alt}?",
            ),
            Attr(
                "approvals",
                ("one reviewer", "two reviewers", "three reviewers"),
                "Each {S} deployment needs sign-off from {v}.",
                "How many reviewers must approve a {S} deployment?",
                "How many people have to sign off a {S} {alt}?",
            ),
        ),
    ),
    "monitoring": Area(
        ("monitoring", "alerting", "observability"),
        (
            Attr(
                "threshold",
                ("70 percent", "80 percent", "85 percent", "90 percent"),
                "{S} monitoring raises a CPU alert above {v}.",
                "At what CPU level does {S} monitoring raise an alert?",
                "What utilisation triggers a {alt} warning on {S}?",
            ),
            Attr(
                "rotation",
                ("7 days", "14 days", "21 days"),
                "The {S} on-call rotation lasts {v}.",
                "How long is the {S} on-call rotation?",
                "For how long does one engineer cover the {S} pager?",
            ),
            Attr(
                "dashboard",
                ("Pulse Board", "Signal Wall", "Health Atlas", "Ops Lens"),
                "The {S} monitoring dashboard is called {v}.",
                "What is the {S} monitoring dashboard called?",
                "Which board displays {alt} data for {S}?",
            ),
        ),
    ),
    "incident": Area(
        ("incident", "outage", "disruption"),
        (
            Attr(
                "sev1",
                ("10 minutes", "15 minutes", "20 minutes", "30 minutes"),
                "Severity 1 incident reports for {S} must receive a first response within {v}.",
                "What is the first response time for a severity 1 {S} incident?",
                "How quickly must someone react to a critical {S} {alt}?",
            ),
            Attr(
                "contact",
                (
                    "the duty manager",
                    "the platform lead",
                    "the service owner",
                    "the security officer",
                ),
                "{S} incident escalations go to {v}.",
                "Who handles {S} incident escalations?",
                "Whom should I contact when a {S} {alt} gets worse?",
            ),
            Attr(
                "postmortem",
                ("3 days", "5 days", "7 days", "10 days"),
                "A written post-mortem for a {S} incident is due within {v}.",
                "When is the {S} incident post-mortem due?",
                "How long after a {S} {alt} must the review document be filed?",
            ),
        ),
    ),
}


@dataclass(frozen=True)
class Document:
    doc_id: str
    title: str
    text: str

    @property
    def indexed_text(self) -> str:
        return f"{self.title}. {self.text}"


@dataclass(frozen=True)
class QAItem:
    qid: str
    question: str
    gold_doc_id: str
    gold_answer: str
    kind: str


@dataclass(frozen=True)
class KnowledgeBase:
    docs: tuple[Document, ...]
    qa: tuple[QAItem, ...]

    def fingerprint(self) -> str:
        payload = json.dumps(
            {"docs": [asdict(d) for d in self.docs], "qa": [asdict(q) for q in self.qa]},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


def _misspell(question: str, avoid: set[str], rng: random.Random, n_words: int = 2) -> str:
    words = question.split()
    candidates = [
        i
        for i, w in enumerate(words)
        if len(w.strip("?")) >= 5 and w.strip("?").lower() not in avoid and w.strip("?").isalpha()
    ]
    for i in rng.sample(candidates, min(n_words, len(candidates))):
        core, tail = words[i].rstrip("?"), words[i][len(words[i].rstrip("?")) :]
        swaps = [j for j in range(1, len(core) - 2) if core[j] != core[j + 1]]
        if swaps:
            j = rng.choice(swaps)
            core = core[:j] + core[j + 1] + core[j] + core[j + 2 :]
        words[i] = core + tail
    return " ".join(words)


def generate_knowledge_base(
    *, n_systems: int = 24, queries_per_kind: int = 50, seed: int = 7
) -> KnowledgeBase:
    if not 1 <= n_systems <= len(SYSTEMS):
        raise ValueError(f"n_systems must be in [1, {len(SYSTEMS)}]")
    rng = random.Random(seed)
    systems = SYSTEMS[:n_systems]
    codes = rng.sample(range(1000, 10000), n_systems * len(AREAS))

    docs: list[Document] = []
    facts: dict[
        str, dict[str, tuple[Attr, str, str]]
    ] = {}  # doc_id -> attr key -> (attr, value, alt)
    code_of: dict[str, str] = {}
    meta: dict[str, tuple[str, str, str]] = {}  # doc_id -> (system, area, unseen synonym)
    for s_idx, system in enumerate(systems):
        for a_idx, (area_name, area) in enumerate(AREAS.items()):
            doc_id = f"doc-{system.lower()}-{area_name}"
            code = f"RB-{codes[s_idx * len(AREAS) + a_idx]}"
            in_doc_syn, unseen_syn = rng.sample(area.terms[1:], 2)
            sentences = [
                f"This policy describes {area.terms[0]} handling for the {system} platform, "
                f"also referred to as {in_doc_syn}.",
                f"Runbook code: {code}.",
            ]
            values = {}
            for attr in area.attrs:
                value = rng.choice(attr.values)
                values[attr.key] = (attr, value, unseen_syn)
                sentences.append(attr.fact.format(S=system, v=value))
            docs.append(Document(doc_id, f"{system} {area_name} policy", " ".join(sentences)))
            facts[doc_id], code_of[doc_id], meta[doc_id] = (
                values,
                code,
                (system, area_name, unseen_syn),
            )

    combos = [(d.doc_id, a.key) for d in docs for a in AREAS[meta[d.doc_id][1]].attrs]
    qa: list[QAItem] = []
    for kind in ("keyword", "natural", "paraphrase", "typo"):
        for n, (doc_id, key) in enumerate(rng.sample(combos, queries_per_kind)):
            attr, value, alt = facts[doc_id][key]
            system = meta[doc_id][0]
            if kind == "keyword":
                question = f"{code_of[doc_id]} {attr.key}"
            elif kind == "natural":
                question = attr.natural.format(S=system)
            elif kind == "paraphrase":
                question = attr.para.format(S=system, alt=alt)
            else:
                question = _misspell(attr.natural.format(S=system), {system.lower()}, rng)
            qa.append(QAItem(f"{kind}-{n:03d}", question, doc_id, value, kind))
    return KnowledgeBase(tuple(docs), tuple(qa))
