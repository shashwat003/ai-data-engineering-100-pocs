# Results (measured by `python -m canary_model_serving.main`; traffic SIMULATED, 200 seeds per scenario, 600 req/min, looks every 5 min)

| candidate | rollback rate | of which deadline (fail closed) | median min to decision | median min to rollback | mean excess errors (canary rollout) | excess errors (big-bang, 60 min) |
|---|---|---|---|---|---|---|
| healthy (identical) | 0.005 | 0.005 | 105.000 | 190.000 | 0.000 | 0.000 |
| mild error rise (1.0% -> 1.3%, inside tolerance) | 0.500 | 0.500 | 190.000 | 190.000 | 105.000 | 108.000 |
| latency +35% | 1.000 | 0.000 | 50.000 | 50.000 | 0.000 | 0.000 |
| error rate 1% -> 3% | 1.000 | 0.000 | 50.000 | 50.000 | 41.500 | 720.000 |
| silent accuracy drop 92% -> 87% | 1.000 | 0.000 | 80.000 | 80.000 | 0.000 | 0.000 |
| latency tail blow-up (sigma 0.3 -> 0.7) | 1.000 | 0.000 | 50.000 | 50.000 | 0.000 | 0.000 |

A rollback "by deadline" means the gate could not PROVE the candidate within tolerance at the 50% step within 120 minutes, so it failed closed. Excess errors = errors beyond the baseline's rate on the same requests. A healthy candidate should (almost) never be rolled back; a regressed one should be, early.
