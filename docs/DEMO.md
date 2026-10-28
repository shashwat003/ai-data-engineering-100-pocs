# Demo instructions

Every POC has a `Makefile` with `test` and `demo` targets; `demo` writes `results/results.md` (and usually `metrics.json`), and `python scripts/embed_results.py` copies those results into the POC's README between the `RESULTS` markers. Nothing needs credentials, Docker, a GPU or a paid service.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt          # core + test tools, exact pins (Python 3.12+)
make -C pocs/098-reference-enterprise-rag demo PY=python
cat pocs/098-reference-enterprise-rag/results/results.md
```

## Tiers
| Tier | POCs | What to install | Run |
|---|---|---|---|
| Light (default) | most | `requirements-dev.txt` (+ `requirements-mlops.txt`, `requirements-edge.txt`, `requirements-logs.txt` for the MLflow, ONNX and OpenTelemetry POCs) | `python scripts/run_tests.py --fast` and `python scripts/run_demos.py --fast` |
| Heavy | 032 Playwright; 033, 040, 050 dbt; 038, 043, 057 Spark; 039 PostgreSQL; 042 Dagster; 051, 052, 053, 054, 058, 062, 063, 064 Terraform or moto; 067 DVC; 076 reproducibility kit; 083 OPA | the matching `requirements-*.txt` (infra, spark, dbt, dvc, dagster, playwright, postgres) plus the tool binaries below | `python scripts/run_tests.py --heavy` and `python scripts/run_demos.py --heavy` |
| Own environment | 041 Airflow 3 | a separate venv: `pip install -r requirements-airflow-lock.txt` (Airflow 3.3.2, exact lock) (see the `airflow` job in `.github/workflows/heavy.yml`) | `python scripts/run_tests.py --own-env`, `python scripts/run_demos.py --own-env` |

Tool binaries for the heavy tier: Java 17+ (Spark), Terraform 1.5+ (051/052/054/057/062/063/064; the demos use plan-only or a local moto server with credentials `mock`), OPA (083), a Chromium install (`playwright install chromium`, 032), PostgreSQL 16 server binaries (039; the demo starts a throw-away server on a unix socket), gitleaks (086). Tests that need a missing tool skip with a reason instead of failing. Set `DEMO_QUICK=1` for the shortened Spark/PostgreSQL demos.

## Suggested ten-minute tour (flagships)
| Order | Command | What to look at |
|---|---|---|
| 1 | `make -C pocs/003-text-to-sql-semantic-layer demo PY=python` | guard outcomes on adversarial SQL, accuracy of the semantic layer |
| 2 | `make -C pocs/098-reference-enterprise-rag demo PY=python` | 14 end-to-end scenarios: grounded answer, blocked injection, PII, erasure, audit chain |
| 3 | `make -C pocs/035-medallion-lakehouse-duckdb demo PY=python` | batch ledger, quarantine counts, replay no-op |
| 4 | `make -C pocs/097-reference-ai-data-platform demo PY=python` | batch-by-batch table, model promotion decisions, tampered-batch fail-closed |
| 5 | `make -C pocs/039-postgres-logical-cdc-to-lake demo PY=python` | reconcile counts after concurrent writes, crash/replay, schema-evolution pitfall, slot lag |
| 6 | `make -C pocs/079-prompt-injection-red-team demo PY=python` | attack success rate per defence layer |
| 7 | `make -C pocs/065-mlflow-registry-promotion demo PY=python` | statistical promotion gate reasons |
| 8 | `make -C pocs/051-terraform-aws-datalake-foundation demo PY=python` | plan/apply against a local AWS mock, policy checks |

## Reproducibility notes
- Seeds are fixed; outputs are deterministic on one machine. Timings in any README are measured once and are noisy; tests never assert timings.
- Results from freshly trained scikit-learn models can differ across CPUs/BLAS builds (see `docs/EVIDENCE.md`); the committed numbers are the reference run.
- Demos write only inside the POC directory (`results/`, sometimes `data/generated/`) or a temporary directory. They make no network calls except to `localhost` services they start themselves.
