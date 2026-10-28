# Results (measured by `python -m llm_serving_study.main`; discrete-event MODEL with assumed GPU cost parameters; no GPU, vLLM or real model)

Assumed cost model: decode iteration 18 ms + 0.4 ms per sequence, prefill 0.25 ms per prompt token, KV cache 40,000 tokens, max batch 64. Prompt median 300 tokens, output median 120 tokens, Poisson arrivals, 240 s.

## 0.5 requests/s (102 requests)

| configuration | tokens/s | TTFT p50 s | TTFT p95 s | TPOT p50 ms | E2E p95 s | max concurrent | KV peak % | preemptions |
|---|---|---|---|---|---|---|---|---|
| no batching | 51.000 | 81.507 | 99.648 | 18.400 | 103.770 | 1 | 5.800 | 0 |
| static batching (8, 250 ms fill) | 70.500 | 2.292 | 8.730 | 18.800 | 12.560 | 7 | 17.700 | 0 |
| continuous, reserved KV | 71.400 | 0.093 | 0.241 | 19.400 | 9.780 | 6 | 12.900 | 0 |
| continuous, paged KV | 71.400 | 0.093 | 0.241 | 19.400 | 9.780 | 6 | 9.200 | 0 |

## 2.0 requests/s (449 requests)

| configuration | tokens/s | TTFT p50 s | TTFT p95 s | TPOT p50 ms | E2E p95 s | max concurrent | KV peak % | preemptions |
|---|---|---|---|---|---|---|---|---|
| no batching | 52.700 | 610.437 | 1034.189 | 18.400 | 1037.230 | 1 | 5.800 | 0 |
| static batching (8, 250 ms fill) | 164.000 | 105.436 | 168.599 | 20.600 | 174.590 | 8 | 22.200 | 0 |
| continuous, reserved KV | 288.500 | 0.119 | 0.292 | 25.600 | 12.390 | 18 | 42.400 | 0 |
| continuous, paged KV | 288.500 | 0.119 | 0.292 | 25.600 | 12.390 | 18 | 25.200 | 0 |

## 6.0 requests/s (1411 requests)

| configuration | tokens/s | TTFT p50 s | TTFT p95 s | TPOT p50 ms | E2E p95 s | max concurrent | KV peak % | preemptions |
|---|---|---|---|---|---|---|---|---|
| no batching | 52.800 | 2047.877 | 3997.078 | 18.400 | 3997.780 | 1 | 5.800 | 0 |
| static batching (8, 250 ms fill) | 164.000 | 579.164 | 1137.801 | 20.600 | 1139.590 | 8 | 25.200 | 0 |
| continuous, reserved KV | 727.900 | 33.753 | 67.837 | 60.000 | 79.860 | 50 | 100.000 | 0 |
| continuous, paged KV | 789.900 | 21.094 | 42.108 | 77.000 | 62.260 | 64 | 91.200 | 0 |

## 12.0 requests/s (2881 requests)

| configuration | tokens/s | TTFT p50 s | TTFT p95 s | TPOT p50 ms | E2E p95 s | max concurrent | KV peak % | preemptions |
|---|---|---|---|---|---|---|---|---|
| no batching | 52.700 | 4432.786 | 8264.756 | 18.400 | 8265.400 | 1 | 5.800 | 0 |
| static batching (8, 250 ms fill) | 162.500 | 1335.376 | 2520.756 | 20.600 | 2523.470 | 8 | 25.300 | 0 |
| continuous, reserved KV | 715.200 | 196.252 | 387.269 | 60.800 | 395.640 | 50 | 100.000 | 0 |
| continuous, paged KV | 783.700 | 167.508 | 331.073 | 80.100 | 343.320 | 64 | 99.800 | 2 |
