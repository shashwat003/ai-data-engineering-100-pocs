# Release summary: 100 Real-World AI & Data Engineering POCs (for publication review)

**Status:** 100 of 100 POCs built, tested and documented against revised acceptance criteria. 71 are implemented as briefed with the named open-source components running locally; 29 are labelled local variants (see [STATUS.md](STATUS.md), column *fidelity*). Private repository, branch `claude/relaxed-allen-usvr0o`; nothing merged, nothing public.

## What the portfolio is
A monorepo of self-contained proof-of-concepts across LLM/RAG, small language models, agents, data engineering, AWS/IaC, MLOps/LLMOps, governance/security, analytics and capstone reference architectures. Every POC has a distinct problem, a Mermaid diagram, automated tests, a reproducible demo whose results are embedded in its README, an *Evidence status* table (real / simulated / mock / modelled / not tested) and a list of limitations. Shared code lives in `libs/`; generated documentation is CI-checked.

## Verification
- Local, final tree: fast suite 81 suites / 1,499 passed / 0 failed / 4 skipped; heavy suite 28 suites / 483 passed / 0 failed / 0 skipped (details in [READINESS.md](READINESS.md), section K).
- GitHub Actions: fast `ci` and `heavy-validation` (including the `airflow` job) green on the release candidate commit (see [CI_REPORT.md](CI_REPORT.md)).
- Repo-wide consistency check (POC 100): 0 problems; catalogue, STATUS and FLAGSHIPS freshness checked in CI; secret scan clean; gitleaks full-history scan clean with the documented `.gitleaksignore`; `pip-audit`: Airflow environment upgraded to 3.3.2 (0 known vulnerabilities); 1 accepted finding in the main environment (`diskcache`, no fix available) - see DEPENDENCY_AUDIT.md.

## What is real, what is not
- **Real, run in tests and demos:** DuckDB, SQLite, PyIceberg, Delta/Parquet, dbt-core, local Spark, MLflow (SQLite), ONNX Runtime, FAISS, Splink, OpenTelemetry SDK, Playwright/Chromium, Terraform plan and apply (against a local mock), OPA, a throw-away PostgreSQL 16 with logical decoding, AWS CDK synthesis (offline), cfn-lint, gitleaks.
- **Mocked or simulated, always labelled:** AWS APIs (moto, literal `mock` credentials), Ollama (a fake HTTP server), queueing/serving/autoscaling models, a simulated stream broker, rule-generated labels, hashing embedders and extractive generators standing in for embedding and generation models.
- **Never run (unverified):** any real LLM or transformer, GPU training, PEFT/bitsandbytes, AWS Bedrock, Redshift, EMR, Lambda, Kinesis, Step Functions, Lake Formation, Kafka/Debezium, OpenSearch Serverless, any deployment. Nothing in this repository touched AWS or any paid service.

## Limitations to state publicly
- Local variants demonstrate engineering mechanisms, not behaviour of the replaced service or model; their numbers must not be quoted as Bedrock, Redshift, EMR or transformer results.
- Synthetic data only; model-quality figures come from toy tasks. Trained-model results can differ across CPUs/BLAS builds (see [EVIDENCE.md](EVIDENCE.md)).
- Single-seed studies are noisy at the one-point level; timings are measured once and never asserted.
- Heavy workflows are path-filtered; optional tools (Java, Terraform, Node, PostgreSQL 16 binaries, OPA, Chromium) are needed to run the heavy tier locally.
- One accepted dependency advisory remains (`diskcache`, DVC POCs only; see [DEPENDENCY_AUDIT.md](DEPENDENCY_AUDIT.md)).

## Licence and attribution
Apache-2.0 draft in `LICENSE` with `NOTICE` and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md); the copyright holder line needs the owner's confirmation. No third-party code or data is vendored.

## Publication
Not performed. The owner-gated steps (licence confirmation, gitleaks allowlist review, removal of planning notes, merge, visibility, profile README, pinning) are in [READINESS.md](READINESS.md), section J.
