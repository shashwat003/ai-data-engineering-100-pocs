# Results: long-context vs RAG on a synthetic policy KB (120 documents, 9671 tokens)

MEASURED: context token counts, whether the gold document reaches the context, retrieval time. MODELLED (assumptions listed in results.json; illustrative, not vendor prices): cost, latency, and how reader accuracy falls with context length. No LLM was called, so `est_accuracy` = gold-in-context x an assumed reliability curve, not a measured answer accuracy.

| reader degradation assumed | LC tokens | LC est. acc | LC $/1k queries | LC s | RAG tokens | RAG est. acc | RAG $/1k queries | RAG s |
|---|---|---|---|---|---|---|---|---|
| no degradation | 9671 | 1.000 | 9.790 | 2.530 | 235.100 | 0.975 | 0.355 | 0.647 |
| mild (3%/doubling) | 9671 | 0.902 | 9.790 | 2.530 | 235.100 | 0.975 | 0.355 | 0.647 |
| steep (8%/doubling) | 9671 | 0.738 | 9.790 | 2.530 | 235.100 | 0.975 | 0.355 | 0.647 |

RAG retrieval time (measured): 0.3 ms/query; RAG gold-document recall@3 by question kind: {'keyword': 1.0, 'natural': 1.0, 'paraphrase': 0.9, 'typo': 1.0}.

If the corpus does not fit the window (2,000 tokens here), long-context stuffing truncates: gold document present for 23% of questions.

RAG recall@3 as the corpus grows (measured): [{'n_docs': 15, 'rag_recall': 0.944}, {'n_docs': 30, 'rag_recall': 0.903}, {'n_docs': 60, 'rag_recall': 0.931}, {'n_docs': 120, 'rag_recall': 0.975}].

| docs | corpus tokens | fits 128k window | long-ctx $/query | RAG $/1k queries | cost ratio |
|---|---|---|---|---|---|
| 100 | 8059 | True | 0.008 | 0.000 | 23.000 |
| 1000 | 80591 | True | 0.081 | 0.000 | 227.400 |
| 10000 | 805916 | False | None | 0.000 | None |
| 100000 | 8059166 | False | None | 0.000 | None |

The projection extrapolates token counts and cost only; retrieval quality at 10k+ documents is not measured.
