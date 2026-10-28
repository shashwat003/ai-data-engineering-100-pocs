# Results: schema-validated extraction on 200 synthetic invoices (8 contain a prompt-injection line)

The extractor is a SIMULATED model (regex reader with seeded, realistic format faults: fenced or trailing-comma JSON, wrong date format, string amounts, wrong total, missing field, bad currency, chatty prose). 55% of first attempts are faulty; with validation feedback a fault is repaired with an assumed 80% probability per retry. The repair rate for a real model is not measured.

| max attempts | valid rate | dead-lettered | exact match (accepted) | mean attempts | input tokens (est.) | injected docs with total=0 |
|---|---|---|---|---|---|---|
| 1 | 0.580 | 84 | 1.000 | 1.000 | 24762 | 0 |
| 2 | 0.880 | 24 | 1.000 | 1.420 | 38249 | 0 |
| 3 | 0.975 | 5 | 1.000 | 1.540 | 42041 | 0 |
| 5 | 0.995 | 1 | 1.000 | 1.575 | 43209 | 0 |

Accepted records are always schema-valid and arithmetically consistent; documents that never validate are dead-lettered, never passed on.
