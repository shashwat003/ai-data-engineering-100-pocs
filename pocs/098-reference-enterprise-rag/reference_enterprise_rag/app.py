"""An enterprise RAG request path assembled from earlier POCs:
rate limit + input guard (034) -> semantic cache (006) -> hybrid retrieval (retrieval_kit, as in 001) ->
cited generation -> citation guard (002) -> output guard (034) -> tagged cache write -> tamper-evident audit (084).
Customer notes live in an encrypted store (088) so they can be erased everywhere, including the cache."""

from __future__ import annotations

import hashlib
import hmac
import re
import sqlite3
import time
from dataclasses import dataclass, field

from agent_policy_guardrails.engine import Guard
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from gdpr_erasure_pipeline.vectors import TicketStore
from rag_citation_guard.guard import guard as citation_guard
from rag_citation_guard.guard import passages, split_sentences
from semantic_cache_gateway.cache import SemanticCache
from tamper_evident_audit_log.log import AuditLog

from retrieval_kit import BM25Retriever, DenseRetriever, HybridRetriever, LSAEmbedder

VISIBILITY = {"employee": {"public"}, "support_agent": {"public", "support"}}
WORD = re.compile(r"[a-z0-9\-]+")
MODEL = "extractive-v1"


@dataclass
class Doc:
    doc_id: str
    text: str
    visibility: str = "public"
    tenant: str = "t1"
    subject: str | None = None


class TaggedCache(SemanticCache):
    """Semantic cache that remembers which documents each cached answer came from, so erasure can purge derived answers."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.tags: dict[tuple, set[str]] = {}

    def put(self, prompt: str, response: str, tenant: str, doc_ids: set[str]) -> bool:
        ok = self.store_response(prompt, response, tenant=tenant, model=MODEL)
        if ok:
            self.tags[(tenant, MODEL, "v1", self._next - 1)] = set(doc_ids)
        return ok

    def invalidate_docs(self, doc_ids: set[str]) -> int:
        n = 0
        for (tenant, model, tpl, eid), tags in list(self.tags.items()):
            if tags & doc_ids:
                b = self._bucket(tenant, model, tpl)
                if eid in b:
                    del b[eid]
                    n += 1
                del self.tags[(tenant, model, tpl, eid)]
        return n

    def all_text(self) -> str:
        return " ".join(
            e.prompt + " " + e.response for b in self.store.values() for e in b.values()
        )


@dataclass
class Result:
    status: str  # answered | blocked_input | blocked_output | rate_limited | no_context | forbidden
    answer: str = ""
    cache_hit: bool = False
    docs: list[str] = field(default_factory=list)
    trace: str = ""
    stages_ms: dict[str, float] = field(default_factory=dict)


class EnterpriseRag:
    def __init__(
        self,
        public_docs: list[Doc],
        ticket_store: TicketStore | None,
        salt: bytes = b"reference-salt-0123456789",
        generator: str = "faithful",
        clock=time.monotonic,
    ):
        self.public, self.tickets, self.salt, self.generator = (
            list(public_docs),
            ticket_store,
            salt,
            generator,
        )
        self.guard = Guard(clock=clock)
        self.cache = TaggedCache(threshold=0.8, ttl_s=3600, clock=clock)
        self.audit = AuditLog(sqlite3.connect(":memory:"), Ed25519PrivateKey.generate())
        self.docs: dict[str, Doc] = {}
        self._seq = 0
        self.reindex()

    # ---- index
    def reindex(self) -> None:
        docs = {d.doc_id: d for d in self.public}
        if self.tickets is not None:
            for r in self.tickets._rows():
                p = self.tickets._open(r)
                if p:
                    docs[r["ticket_id"]] = Doc(
                        r["ticket_id"], p["text"], "support", "t1", r["subject"]
                    )
        self.docs = docs
        ids, texts = list(docs), [d.text for d in docs.values()]
        bm25 = BM25Retriever(ids, texts)
        self.retriever = HybridRetriever(bm25, DenseRetriever(ids, texts, LSAEmbedder(dims=32)))

    def index_text(self) -> str:
        return " ".join(d.text for d in self.docs.values())

    # ---- request path
    def _pseudo(self, user: str) -> str:
        return hmac.new(self.salt, user.encode(), hashlib.sha256).hexdigest()[:10]

    def _audit(
        self, trace: str, user: str, role: str, status: str, hit: bool, docs: list[str], q: str
    ) -> None:
        self.audit.append(
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(0)),
            self._pseudo(user),
            status,
            {
                "trace": trace,
                "role": role,
                "cache_hit": hit,
                "docs": docs,
                "q_hash": hashlib.sha256(q.encode()).hexdigest()[:12],
            },
        )

    def ask(
        self,
        question: str,
        user: str,
        role: str = "employee",
        tenant: str = "t1",
        case_subject: str | None = None,
    ) -> Result:
        self._seq += 1
        trace = f"tr-{self._seq:05d}"
        res = Result("answered", trace=trace)
        t = time.perf_counter()

        def lap(name):
            nonlocal t
            now = time.perf_counter()
            res.stages_ms[name] = round((now - t) * 1000, 2)
            t = now

        if role not in VISIBILITY:
            res.status = "forbidden"
            self._audit(trace, user, role, res.status, False, [], question)
            return res
        ok, why = self.guard.admit(user, len(question.split()) * 2)
        lap("admit")
        if not ok:
            res.status = "rate_limited"
            self._audit(trace, user, role, res.status, False, [], question)
            return res
        d = self.guard.check(question, "input")
        lap("input_guard")
        if d.action in ("block", "escalate"):
            res.status = "blocked_input"
            self._audit(trace, user, role, res.status, False, [], question)
            return res
        q = d.text  # redacted question: cards and IDs never reach retrieval, cache or the model
        # The cache bucket carries the whole authorisation scope (tenant, role, customer of the open case): an answer built with one
        # scope's documents must never be served to another. A first version keyed by tenant only let an employee's answer be
        # replayed to a support agent who should have seen customer notes, and would have replayed one customer's notes to another.
        bucket = f"{tenant}|{role}|{case_subject}"
        hit, _ = self.cache.lookup(q, tenant=bucket, model=MODEL)
        lap("cache_lookup")
        if hit is not None:
            res.answer, res.cache_hit = hit, True
            self._audit(trace, user, role, "answered", True, [], question)
            return res
        scored = [
            x for x, _ in self.retriever.search(q, len(self.docs))
        ]  # full ranking, then ACL filter (a vector DB would pre-filter by metadata)
        ok = [
            self.docs[i] for i in scored if self._may_read(self.docs[i], role, tenant, case_subject)
        ]
        # multi-source context: up to 2 of the open customer's own notes plus up to 2 public policy documents
        allowed = [d for d in ok if d.subject is not None][:2] + [
            d for d in ok if d.subject is None
        ][:2]
        lap("retrieve")
        if not allowed:
            res.status = "no_context"
            self._audit(trace, user, role, res.status, False, [], question)
            return res
        res.docs = [x.doc_id for x in allowed]
        draft = self._generate(q, allowed)
        lap("generate")
        g = citation_guard(draft, passages(allowed))
        lap("citation_guard")
        if g["blocked"]:
            res.status = "blocked_output"
            self._audit(trace, user, role, res.status, False, res.docs, question)
            return res
        out = self.guard.check(g["text"], "output")
        lap("output_guard")
        if out.action in ("block", "escalate"):
            res.status = "blocked_output"
            self._audit(trace, user, role, res.status, False, res.docs, question)
            return res
        res.answer = out.text
        self.cache.put(q, res.answer, bucket, set(res.docs))
        lap("cache_store")
        self._audit(trace, user, role, "answered", False, res.docs, question)
        return res

    @staticmethod
    def _may_read(d: Doc, role: str, tenant: str, case_subject: str | None) -> bool:
        """Least privilege: public documents for everyone; customer notes only for support agents, only for the customer of the open case."""
        if d.tenant != tenant or d.visibility not in VISIBILITY[role]:
            return False
        return d.subject is None or d.subject == case_subject

    def _generate(self, question: str, docs: list[Doc]) -> str:
        """Extractive 'model': the best-matching sentence of each context, with a [doc#sentence] citation. No LLM is used."""
        qw = set(WORD.findall(question.lower()))
        out = []
        for d in docs:
            sents = split_sentences(d.text)
            best = max(
                range(len(sents)), key=lambda i: len(qw & set(WORD.findall(sents[i].lower())))
            )
            out.append(f"{sents[best].rstrip('.')}. [{d.doc_id}#{best}]")
        if self.generator == "hallucinating":
            out.append(f"The retention period is 400 days. [{docs[0].doc_id}#0]")
        return " ".join(out)
