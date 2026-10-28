# Results (POC 099: REAL int8 ONNX edge model + REAL drift monitor/router; cloud latency and price MODELLED; no language model)
Edge accuracy 0.841, cloud accuracy 0.8913 on 3,000 validation requests; calibrated threshold (within 1 point of cloud-only): **0.75**. Measured edge median latency: 0.04 ms (noisy, not asserted). Cloud RTT 120 ms, timeout 400 ms and 0.0004 USD per call are ASSUMED.

## Policies on a clean stream

| policy | accuracy | cloud share | mean latency ms (modelled) | p95 ms | USD per 1k |
|---|---|---|---|---|---|
| edge only | 0.851 | 0.00 | 0.04 | 0.0 | 0.000 |
| hybrid (threshold 0.75) | 0.880 | 0.32 | 38.28 | 120.0 | 0.127 |
| cloud only | 0.881 | 1.00 | 120.04 | 120.0 | 0.400 |

## Cloud outage (requests 300-699 of 1,500)

| router | accuracy | cloud share | degraded (edge fallback) share | mean latency ms (modelled) | p95 ms |
|---|---|---|---|---|---|
| with circuit breaker | 0.873 | 0.22 | 0.09 | 30.4 | 121.0 |
| without circuit breaker | 0.873 | 0.23 | 0.09 | 63.3 | 401.0 |

## Traffic-mix drift towards hard cases (onset after window 4)

| scenario | accuracy after onset | cloud share after onset | windows to first alert | false-alert windows before onset |
|---|---|---|---|---|
| 50% hard cases, static threshold | 0.859 | 0.363 | 2 | 0 |
| 50% hard cases, controlled | 0.866 | 0.785 | 2 | 0 |
| 100% hard cases, static threshold | 0.828 | 0.420 | 2 | 0 |
| 100% hard cases, controlled | 0.836 | 0.829 | 2 | 0 |
