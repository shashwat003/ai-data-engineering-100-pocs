# Results (measured by `python -m llm_tracing_otel.main`; backend SIMULATED, prices illustrative)

1200 spans, 400 LLM spans, LLM spans with a request parent: 400/400

| route|model | calls | error rate | cost USD | cost/call | p50 ms | p95 ms |
|---|---|---|---|---|---|---|
| faq|small-model | 155 | 3.9% | 0.0363 | 0.00024 | 133 | 157 |
| legacy-bloated|large-model | 34 | 5.9% | 0.4621 | 0.01444 | 1493 | 1607 |
| report|large-model | 89 | 3.4% | 0.2826 | 0.00329 | 595 | 735 |
| search|small-model | 122 | 3.3% | 0.0452 | 0.00038 | 246 | 270 |

## Alerts

- budget exceeded: $0.8262 > $0.5000
- legacy-bloated|large-model: cost/call 0.01444 is 7.9x the median route

- traceparent propagation keeps one trace across a service hop: True
- prompt text captured by default: False
