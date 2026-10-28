# Results: multi-hop retrieval on a synthetic corpus (270 passages, 90 questions)

Metric: share of questions whose retrieved context (top 6 passages) contains ALL gold supporting passages. No generator model is involved, so this measures retrieval only, not answer quality. Relation extraction by regex recovered 210 of 210 true facts (60 passages unmatched, the rest are filler). Edge-loss rows randomly drop extracted relations to mimic a weaker extractor.

| method | 1-hop | 2-hop | 3-hop |
|---|---|---|---|
| bm25 | 0.77 | 0.03 | 0.00 |
| dense_lsa | 0.43 | 0.07 | 0.00 |
| hybrid_rrf | 0.60 | 0.07 | 0.00 |
| graph (BM25 edge scoring), edge loss 0% | 0.57 | 0.27 | 0.20 |
| graph (BM25 edge scoring), edge loss 20% | 0.49 | 0.21 | 0.10 |
| graph (BM25 edge scoring), edge loss 40% | 0.47 | 0.20 | 0.09 |
| graph + relation plan, edge loss 0% | 1.00 | 1.00 | 0.70 |
| graph + relation plan, edge loss 20% | 0.80 | 0.62 | 0.40 |
| graph + relation plan, edge loss 40% | 0.62 | 0.40 | 0.23 |

Caveat: the corpus is templated with three paraphrases per relation, so the rule-based extractor is far more accurate than a real one on messy text.
