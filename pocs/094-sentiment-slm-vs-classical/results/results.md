# Results (measured by `python -m sentiment_slm_vs_classical.main`; synthetic reviews, 5% label noise)

| model | accuracy | 95% CI | macro-F1 | acc easy | acc hard | ms / 1k reviews |
|---|---|---|---|---|---|---|
| lexicon | 0.615 | 0.578-0.647 | 0.599 | 0.903 | 0.209 | 4.600 |
| naive_bayes_unigram | 0.847 | 0.818-0.875 | 0.844 | 0.909 | 0.759 | 12.800 |
| logreg_unigram | 0.903 | 0.88-0.925 | 0.903 | 0.954 | 0.831 | 10.600 |
| logreg_bigram | 0.945 | 0.928-0.962 | 0.935 | 0.954 | 0.932 | 15.000 |

- SLM arm: **NOT RUN** no local Ollama reachable (LLMError); SLM arm unverified
- Modelled SLM cost: $0.035 per 1k reviews (assumed $0.0005/1k tokens x 70 tokens); classical models cost ~nothing at inference.
