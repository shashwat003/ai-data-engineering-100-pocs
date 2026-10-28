"""POC 009: plain retrieval vs graph-guided retrieval on 1/2/3-hop questions (retrieval-level metrics)."""

from __future__ import annotations

import random
from pathlib import Path

from pocs_common import write_json
from pocs_common.report import markdown_table
from retrieval_kit import BM25Retriever, DenseRetriever, HybridRetriever, LSAEmbedder

from .corpus import build
from .graph import GraphRetriever, extract_relations

POC_DIR = Path(__file__).resolve().parent.parent
K = 6


def support_recall(retrieved: list[str], gold: set[str]) -> tuple[float, bool]:
    got = len(gold & set(retrieved))
    return got / len(gold), got == len(gold)


def evaluate(c: dict, retriever, k: int = K) -> dict:
    by: dict[int, list[tuple[float, bool]]] = {1: [], 2: [], 3: []}
    for q in c["questions"]:
        r = retriever(q["q"])[:k]
        by[q["hops"]].append(support_recall(r, q["gold"]))
    return {
        h: {"recall": round(sum(x[0] for x in v) / len(v), 4), "all_support": round(sum(x[1] for x in v) / len(v), 4), "n": len(v)}
        for h, v in by.items()
    }  # fmt: skip


def run(n_companies: int = 60, seed: int = 8, drop_rates=(0.0, 0.2, 0.4)) -> dict:
    c = build(n_companies, seed)
    ids, texts = [p["id"] for p in c["passages"]], [p["text"] for p in c["passages"]]
    bm = BM25Retriever(ids, texts)
    emb = LSAEmbedder(dims=64)
    dense = DenseRetriever(ids, texts, emb)
    hyb = HybridRetriever(bm, dense)
    rels, miss = extract_relations(c["passages"])
    out = {"n_passages": len(ids), "n_questions": len(c["questions"]), "k": K, "extraction_unmatched": miss,
           "relations_extracted": len(rels), "relations_true": len(c["facts"]), "methods": {}}  # fmt: skip
    for name, r in (("bm25", bm), ("dense_lsa", dense), ("hybrid_rrf", hyb)):
        out["methods"][name] = evaluate(c, lambda q, r=r: [d for d, _ in r.search(q, K)])
    for plan in (False, True):
        for dr in (
            drop_rates
        ):  # simulate extraction misses by dropping relations at random (mean of 3 seeds)
            runs = []
            for seed_ in (1, 2, 3):
                rng = random.Random(seed_)
                kept = [r for r in rels if rng.random() >= dr]
                g = GraphRetriever(c["passages"], kept, use_plan=plan)
                runs.append(evaluate(c, lambda q, g=g: g.search(q, K)))
            avg = {h: {"recall": round(sum(r[h]["recall"] for r in runs) / 3, 4), "all_support": round(sum(r[h]["all_support"] for r in runs) / 3, 4), "n": runs[0][h]["n"]} for h in (1, 2, 3)}  # fmt: skip
            label = "graph + relation plan" if plan else "graph (BM25 edge scoring)"
            out["methods"][f"{label}, edge loss {int(dr * 100)}%"] = avg
    return out


def main() -> None:
    res = run()
    write_json(POC_DIR / "results" / "results.json", res)
    rows = [
        (m, *[f"{v[h]['all_support']:.2f}" for h in (1, 2, 3)]) for m, v in res["methods"].items()
    ]
    md = (
        f"# Results: multi-hop retrieval on a synthetic corpus ({res['n_passages']} passages, {res['n_questions']} questions)\n\n"
        f"Metric: share of questions whose retrieved context (top {res['k']} passages) contains ALL gold supporting passages. "
        f"No generator model is involved, so this measures retrieval only, not answer quality. Relation extraction by regex "
        f"recovered {res['relations_extracted']} of {res['relations_true']} true facts ({res['extraction_unmatched']} passages unmatched, "
        f"the rest are filler). Edge-loss rows randomly drop extracted relations to mimic a weaker extractor.\n\n"
        + markdown_table(["method", "1-hop", "2-hop", "3-hop"], rows)
        + "\n\nCaveat: the corpus is templated with three paraphrases per relation, so the rule-based extractor is "
        "far more accurate than a real one on messy text.\n"
    )
    (POC_DIR / "results" / "results.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
