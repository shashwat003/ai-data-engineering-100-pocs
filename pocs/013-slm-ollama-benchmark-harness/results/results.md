# Results: SLM benchmark harness run against a FAKE Ollama server

WARNING: the backend here is a local fake HTTP server with scripted accuracy, latency and memory. These numbers demonstrate that the harness works; they say nothing about any real model. No real Ollama server or model was available, so the real-model path is UNVERIFIED.

30 tasks x 3 repeats per model. Skipped: {'llama3.2:3b': 'not installed'}.

| model | pass rate | 95% CI | p50 ms | p95 ms | tokens/s | memory MB | Pareto-optimal |
|---|---|---|---|---|---|---|---|
| fake-mid:7b | 0.933 | 0.862-0.969 | 18.200 | 18.700 | 55.600 | 5200.000 | yes |
| fake-small:3b | 0.800 | 0.706-0.87 | 9.000 | 9.800 | 425.300 | 2300.000 | yes |
| fake-wasteful:7b | 0.700 | 0.599-0.785 | 22.100 | 22.700 | 177.200 | 6100.000 | no |
| fake-tiny:1b | 0.633 | 0.53-0.726 | 4.800 | 5.300 | 783.600 | 900.000 | yes |
