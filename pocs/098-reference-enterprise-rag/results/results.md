# Results: end-to-end scenarios on the reference RAG service (extractive generator, no LLM; synthetic data)

| scenario | outcome |
|---|---|
| grounded answer with citations, second user served from cache | answered, cites ['doc-driftwood-deployment', 'doc-beacon-access'], cache hit True |
| prompt injection in the question | blocked_input |
| card number in the question | answered; card in cache: False; in audit: False |
| other tenant gets a cache hit from tenant 1 | False |
| customer notes seen by: employee / support agent without a case / support agent with the case / support agent for ANOTHER customer's notes | False / False / True / False |
| cache reuse across customers (second customer's cache hit) | False |
| unknown role | forbidden |
| hallucinating generator | answered; unsupported sentence removed: True |
| rate limit: request number that was refused | 20 |
| erasure WITHOUT cache purge: stale answer still served | True |
| erasure with cache purge: pipeline status, docs removed, cache entries purged | completed, 2, 1 |
| after erasure: identifiers still visible in index or cache | [] |
| after erasure: same question | answered; subject in answer: False |
| other subjects unaffected; audit rows; audit holds name or email | True; 2; False |
| PII gate on the declared catalogue | True |
| PII gate after an unannounced phone column | False, flagged [('customer_notes', 'backup_phone', 'phone')] |

Stage latencies for one uncached request (ms): {'admit': 0.03, 'input_guard': 0.08, 'cache_lookup': 0.02, 'retrieve': 0.56, 'generate': 0.32, 'citation_guard': 0.3, 'output_guard': 0.11, 'cache_store': 0.16}.
