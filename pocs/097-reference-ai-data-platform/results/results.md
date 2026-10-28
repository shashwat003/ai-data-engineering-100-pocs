# Results (POC 097: real DuckDB/MLflow/OTel/vector-index components wired together; change source is file batches, labels are rule-simulated; nothing on AWS)
200 customers indexed; champion model version after four batches: **v3** (4 versions registered). Incremental embedding: 228 chunks vs about 800 if the index were rebuilt after every batch.

## Batch by batch

| batch | status | lakehouse gates passed | chunks embedded | chunks unchanged | model decisions |
|---|---|---|---|---|---|
| b001 | ok | 8/8 | 169 | 0 | - |
| b002 | ok | 8/8 | 34 | 163 | v1 (regular): PROMOTED (no champion yet: first version passes smoke test) |
| b003 | ok | 8/8 | 12 | 187 | v2 (regular): rejected (subgroup 1 AUC regressed 0.606 -> 0.575) |
| b004 | ok | 8/8 | 13 | 187 | v3 (regular): PROMOTED (improvement +0.2440..+0.5253 above margin; no subgroup regression); v4 (missing feature): rejected (signature mismatch: candidate expects 5 features, serving sends 6) |

## Replaying every batch (idempotency)

| check | result |
|---|---|
| all_ok | True |
| ingested | 0 |
| embedded | 0 |
| models_trained | 0 |
| lake_hash_unchanged | True |
| model_versions_unchanged | True |

## Tampered batch (same id, different bytes)

| check | result |
|---|---|
| status | failed |
| error | BatchConflict: orders/b002 already ingested with different content |
| state_unchanged | True |

Self-retrieval (each customer's profile text finds its own document at rank 1): 100.0%.


## Trace

| span | count |
|---|---|
| lakehouse.run | 9 |
| model.gate | 8 |
| platform.batch | 9 |
| vector.sync | 8 |

Batches whose root span carries an error attribute: 1.
