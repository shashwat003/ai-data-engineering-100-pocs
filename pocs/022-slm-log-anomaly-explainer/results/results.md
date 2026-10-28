# Results: log anomaly detection and guarded explanation (11580 synthetic log lines, 11 Drain templates)

Drain3 (real) mines templates; rule-based detectors flag new templates, rate spikes and lost heartbeats against a 60-minute baseline. The explainer is a SIMULATED small model (keyword rules); no Ollama model was run.

Window-level detection: precision 1.0, recall 0.875, false-positive windows 0, missed windows [135, 136].

| first minute | reason | severity | source | likely cause |
|---|---|---|---|---|
| 85 | new_template:9 | high | model | lock contention between concurrent transactions |
| 120 | spike:8 | medium | model | an upstream dependency is slow or unreachable |
| 137 | lost:6 | medium | model | the database heartbeat stopped reporting |
| 150 | new_template:10 | high | model | a worker process exhausted memory and was killed |
| 165 | new_template:11 | medium | fallback | unknown (automatic fallback) |

Hostile log line (prompt injection) against a deliberately gullible model: reply accepted from model = False (source: fallback). Malformed model output: source = fallback.
