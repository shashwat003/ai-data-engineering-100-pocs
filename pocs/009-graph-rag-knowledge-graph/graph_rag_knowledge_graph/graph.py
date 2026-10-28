"""Rule-based relation extraction into a networkx graph, and iterative graph-guided retrieval."""

from __future__ import annotations

import re

import networkx as nx

from retrieval_kit import BM25Index

PATTERNS = {
    "acquired": [
        (r"^(?P<a>.+?) acquired (?P<b>.+?) in \d{4}\.$", "ab"),
        (r"^In \d{4}, (?P<b>.+?) was bought by (?P<a>.+?)\.$", "ab"),
        (r"^(?P<b>.+?) became part of (?P<a>.+?) after the \d{4} takeover\.$", "ab"),
    ],
    "supplies": [
        (r"^(?P<a>.+?) supplies components to (?P<b>.+?)\.$", "ab"),
        (r"^(?P<b>.+?) sources its parts from (?P<a>.+?)\.$", "ab"),
        (r"^(?P<a>.+?) is the main supplier of (?P<b>.+?)\.$", "ab"),
    ],
    "ceo": [
        (r"^(?P<a>.+?) is the chief executive of (?P<b>.+?)\.$", "ab"),
        (r"^(?P<b>.+?) is led by CEO (?P<a>.+?)\.$", "ab"),
        (r"^(?P<a>.+?) runs (?P<b>.+?) as its chief executive\.$", "ab"),
    ],
    "hq": [
        (r"^(?P<a>.+?) is headquartered in (?P<b>.+?)\.$", "ab"),
        (r"^The head office of (?P<a>.+?) is in (?P<b>.+?)\.$", "ab"),
        (r"^(?P<a>.+?) has its headquarters in the city of (?P<b>.+?)\.$", "ab"),
    ],
}
CUES = {  # question wording -> relation kind (a rule-based stand-in for an LLM query planner)
    "acquired": r"\bacquir\w*|\bbought\b|\btakeover\b",
    "supplies": r"\bsuppl\w*|\bsources?\b",
    "ceo": r"\bchief executive\b|\bCEO\b|\bled by\b",
    "hq": r"\bheadquarter\w*|\bhead office\b",
}
SUFFIXES = (" Corp", " Inc", " Ltd", " Group")


def canon(name: str) -> str:
    for s in SUFFIXES:
        if name.endswith(s):
            return name[: -len(s)]
    return name


def extract_relations(passages: list[dict]) -> tuple[list[dict], int]:
    """Return (relations, number of passages no pattern matched)."""
    rels, miss = [], 0
    for p in passages:
        for kind, pats in PATTERNS.items():
            m = next((re.match(rx, p["text"]) for rx, _ in pats if re.match(rx, p["text"])), None)
            if m:
                rels.append({"kind": kind, "a": canon(m["a"]), "b": canon(m["b"]), "pid": p["id"]})
                break
        else:
            miss += 1
    return rels, miss


def build_graph(rels: list[dict]) -> nx.MultiGraph:
    g = nx.MultiGraph()
    for r in rels:
        g.add_edge(r["a"], r["b"], pid=r["pid"], kind=r["kind"])
    return g


def link_entities(question: str, g: nx.MultiGraph) -> list[str]:
    """Exact whole-word match of known entity names (company names are capitalised tokens)."""
    found = [n for n in g.nodes if re.search(rf"\b{re.escape(n)}\b", question)]
    return sorted(found, key=len, reverse=True)


def relation_plan(question: str) -> list[str]:
    """Relation kinds in hop order: the last-mentioned relation is applied first ("CEO of the supplier of X")."""
    pos = []
    for kind, rx in CUES.items():
        m = re.search(rx, question, re.I)
        if m:
            pos.append((m.start(), kind))
    return [k for _, k in sorted(pos, reverse=True)]


class GraphRetriever:
    """Iterative expansion: from linked entities, take the best-matching edges, then expand again."""

    def __init__(
        self,
        passages: list[dict],
        rels: list[dict],
        hops: int = 3,
        beam: int = 2,
        use_plan: bool = False,
    ):
        self.text = {p["id"]: p["text"] for p in passages}
        self.g = build_graph(rels)
        self.hops, self.beam, self.use_plan = hops, beam, use_plan
        self.bm = BM25Index().fit([p["id"] for p in passages], [p["text"] for p in passages])

    def _score(self, question: str) -> dict[str, float]:
        return dict(self.bm.search(question, k=len(self.text)))

    def search(self, question: str, k: int = 6) -> list[str]:
        score = self._score(question)
        frontier, seen_e, seen_n, out = link_entities(question, self.g), set(), set(), []
        seen_n.update(frontier)
        plan = relation_plan(question) if self.use_plan else []
        for hop in range(self.hops):
            nxt = []
            for n in frontier:
                edges = [
                    (u, v, d["pid"])
                    for u, v, d in self.g.edges(n, data=True)
                    if d["pid"] not in seen_e
                ]
                if plan:  # follow the planned relation; BM25 only breaks ties
                    want = plan[hop] if hop < len(plan) else None
                    kind_of = {d["pid"]: d["kind"] for _, _, d in self.g.edges(n, data=True)}
                    edges.sort(key=lambda e: (kind_of[e[2]] != want, -score.get(e[2], 0)))
                else:
                    edges.sort(key=lambda e: -score.get(e[2], 0))
                if plan and hop >= len(plan):
                    break
                for u, v, pid in edges[: 1 if plan else self.beam]:
                    seen_e.add(pid)
                    out.append(pid)
                    other = v if u == n else u
                    if other not in seen_n:
                        seen_n.add(other)
                        nxt.append(other)
            frontier = nxt
            if not frontier:
                break
        return out[:k]
