# Results: 13 planted secrets (tuning corpus), 7 hold-out secrets in formats not used for tuning, 12 look-alike negatives; all generated at run time

| tool | recall (tuning corpus) | recall (hold-out formats) | hold-out missed | false-positive rate | tuning-corpus missed | seconds |
|---|---|---|---|---|---|---|
| kit scanner | 1.000 | 0.429 | azure-connection-string, bearer-header, private-key-body, split-token | 0.000 | - | 0.003 |
| gitleaks 8.30.0 | 0.923 | 0.714 | private-key-body, split-token | 0.000 | db-url-password | 0.710 |

Both tools see the same files. gitleaks rows appear only when the binary is installed (pinned release in CI). The kit scanner was developed against the tuning corpus, so its score there is optimistic; the hold-out column is the fairer comparison. gitleaks was not tuned on either. Corpora this small are a smoke test, not a benchmark.
