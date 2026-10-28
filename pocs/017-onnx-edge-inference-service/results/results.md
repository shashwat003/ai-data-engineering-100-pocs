# Results: MLP served on CPU through ONNX Runtime (one thread)

Synthetic 20-feature binary task, 1500 held-out rows; latencies are wall-clock on this machine and vary run to run.

| variant | accuracy | AUC | max abs diff vs sklearn | size KB | batch=1 p50 ms | batch=1 p99 ms | batch=32 p50 ms |
|---|---|---|---|---|---|---|---|
| sklearn fp32 | 0.836 | 0.914 | 0.000 | - | 0.238 | 0.469 | 0.343 |
| onnx fp32 | 0.836 | 0.914 | 0.000 | 151.400 | 0.029 | 0.113 | 0.081 |
| onnx int8 (dynamic) | 0.837 | 0.914 | 0.032 | 42.500 | 0.040 | 0.224 | 0.061 |

HTTP service (in-process TestClient, int8 model, 400 single-row requests): p50 0.426 ms, p95 0.601 ms, p99 0.738 ms against a 50.0 ms p99 budget: SLO met = True. This excludes network and process-boundary cost.
