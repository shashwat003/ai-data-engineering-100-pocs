# POC Catalogue (generated from `catalogue/pocs.csv` - do not edit by hand)

100 POCs, **100 built**. `runs_local`: **yes** = no cloud account needed, **partial** = core runs locally, GPU/extra services optional, **aws** = needs an AWS account (cost-guarded, `make destroy` provided).

| Category | Count |
|---|---|
| LLM & RAG | 12 |
| SLM | 10 |
| Agents & Automation | 12 |
| Data Engineering | 16 |
| AWS & Cloud | 14 |
| MLOps & LLMOps | 12 |
| Governance & Security | 12 |
| ML & Analytics | 8 |
| Capstone | 4 |

## LLM & RAG

| # | POC | What it proves | Stack | Runs | Wave | Fidelity | Status |
|---|---|---|---|---|---|---|---|
| 1 | `rag-hybrid-search-eval` | Hybrid BM25+vector RAG with retrieval evaluation harness | Python, pgvector, rank-bm25, pytest | yes | 1 | full | built |
| 2 | `rag-citation-guard` | RAG with enforced citations and groundedness checks | Python, FastAPI, NLI model | yes | 2 | full | built |
| 3 | `text-to-sql-semantic-layer` | NL-to-SQL over a semantic layer with query validation and guardrails | DuckDB, sqlglot, LLM API/Ollama | yes | 1 | full | built |
| 4 | `structured-extraction-pipeline` | Invoice/contract extraction to validated JSON with retries | Python, Pydantic v2 | yes | 2 | full | built |
| 5 | `llm-router-cost-optimiser` | Confidence-based cascade router (small model first, escalate to large model) | FastAPI, Ollama, cost ledger | yes | 1 | full | built |
| 6 | `semantic-cache-gateway` | LLM gateway with semantic cache budgets and rate limits | FastAPI, Redis, embeddings | yes | 2 | full | built |
| 7 | `prompt-regression-testing` | Prompt versioning and CI regression tests with golden sets | pytest, GitHub Actions, YAML prompts | yes | 2 | full | built |
| 8 | `llm-as-judge-calibration` | LLM-as-judge pairwise eval calibrated against human labels | Python, scipy, judge prompts | yes | 3 | full | built |
| 9 | `graph-rag-knowledge-graph` | GraphRAG over an entity graph vs plain vector RAG | Python, networkx, BM25/LSA (no spaCy/Neo4j) | yes | 3 | full | built |
| 10 | `pdf-layout-table-qa` | PDF layout and table-aware question answering (pdfplumber; no vision model) | pdfplumber, reportlab, BM25 | yes | 3 | local-variant | built |
| 11 | `conversational-analytics-bot` | Chat over governed metrics with safe tool calls | Python, DuckDB, pandas | yes | 3 | full | built |
| 12 | `long-context-vs-rag-benchmark` | Benchmark: long-context stuffing vs RAG on cost/latency/accuracy | Python, retrieval_kit (modelled cost/latency) | yes | 3 | full | built |

## SLM

| # | POC | What it proves | Stack | Runs | Wave | Fidelity | Status |
|---|---|---|---|---|---|---|---|
| 13 | `slm-ollama-benchmark-harness` | Benchmark harness for small models (latency/quality/memory) | Python, pandas, Ollama client (fake server in demo) | yes | 2 | full | built |
| 14 | `gguf-quantisation-study` | Block-quantisation trade-off study: llama.cpp-style Q8/Q5/Q4/Q3/Q2 formats on a small language model (no LLM, no llama.cpp) | NumPy, scikit-learn | yes | 3 | local-variant | built |
| 15 | `qlora-ticket-classifier` | Local variant: QLoRA mechanics for ticket classification on CPU - NumPy LoRA with a 4-bit frozen base (no transformer, PEFT, bitsandbytes or GPU) | PEFT, transformers, MLflow | partial | 1 | local-variant | built |
| 16 | `llm-to-slm-distillation` | Distilling a heavy teacher's labels into a tiny task model (scikit-learn ensemble teacher; no transformers) | scikit-learn, NumPy | partial | 2 | local-variant | built |
| 17 | `onnx-edge-inference-service` | Quantised ONNX model served on CPU with latency SLOs | scikit-learn, ONNX Runtime, skl2onnx, FastAPI | yes | 3 | full | built |
| 18 | `slm-pii-redaction` | On-device PII detection and redaction | Python, scikit-learn, pattern validators | yes | 2 | full | built |
| 19 | `slm-function-calling-finetune` | Local variant: reliable tool calling on CPU - LoRA router, slot tagger and strict JSON-Schema gate (no language model generates the calls) | PEFT, JSON-schema eval | partial | 3 | local-variant | built |
| 20 | `speculative-decoding-demo` | Speculative decoding on toy language models: losslessness and speed-up (NumPy; llama.cpp/transformers not used) | NumPy | partial | 3 | local-variant | built |
| 21 | `llm-serving-scheduler-study` | LLM serving scheduling study: static vs continuous batching and paged KV cache in a discrete-event model (vLLM, Locust and GPUs not used) | Python | partial | 3 | local-variant | built |
| 22 | `slm-log-anomaly-explainer` | Small model explains anomalous log clusters | drain3, Python (simulated small model) | yes | 3 | full | built |

## Agents & Automation

| # | POC | What it proves | Stack | Runs | Wave | Fidelity | Status |
|---|---|---|---|---|---|---|---|
| 23 | `incident-triage-agent` | Incident triage agent with policy-gated tools, human approval and audit trail | Python, MCP client (POC 024), SQLite audit log | yes | 1 | full | built |
| 24 | `mcp-server-data-catalog` | MCP server exposing a data catalogue read-only | MCP SDK, DuckDB, JSON schema | yes | 1 | full | built |
| 25 | `mcp-server-repo-ops-readonly` | Read-only MCP server for repo/CI insight | Python, MCP (hand-written core), mock GitHub | yes | 3 | full | built |
| 26 | `multi-agent-dq-fixer` | Multi-agent data-quality triage and proposed fixes | Python, pandas (own agent graph) | yes | 3 | full | built |
| 27 | `agent-code-sandbox` | Sandboxed code-execution tool for agents | Docker, seccomp, FastAPI | yes | 2 | full | built |
| 28 | `agent-trajectory-eval` | Trajectory-level agent evaluation and scoring | Python, OpenTelemetry | yes | 2 | full | built |
| 29 | `human-in-loop-approval-workflow` | Approval gates for agent actions with audit trail | FastAPI, SQLite, webhooks | yes | 2 | full | built |
| 30 | `email-to-ticket-automation` | Parse emails into structured tickets with routing | Python email, Pydantic (mock mailbox, rule classifier) | yes | 3 | full | built |
| 31 | `scheduled-insight-report-agent` | Scheduled metric narrative reports | Python, Jinja2, SQLite (simulated writer) | yes | 3 | full | built |
| 32 | `playwright-web-extractor` | Resilient browser-automation data extraction | Playwright (Chromium), Pydantic | yes | 3 | full | built |
| 33 | `dbt-pr-review-agent` | Agent reviewing dbt model changes in PRs | Python, sqlglot, dbt-duckdb (ground truth) | yes | 3 | full | built |
| 34 | `agent-policy-guardrails` | Policy engine limiting agent tools and data scope | OPA/Rego, Python | yes | 2 | full | built |

## Data Engineering

| # | POC | What it proves | Stack | Runs | Wave | Fidelity | Status |
|---|---|---|---|---|---|---|---|
| 35 | `medallion-lakehouse-duckdb` | Bronze/silver/gold lakehouse: incremental, idempotent, SCD2, contract-gated | DuckDB, Parquet, data contracts (POC 077) | yes | 1 | full | built |
| 36 | `delta-lake-cdc-merge` | Delta Lake CDC ingestion with idempotent MERGE, tombstones, time travel and compaction | delta-rs, PyArrow | yes | 2 | full | built |
| 37 | `exactly-once-stream-windows` | Streaming windowed aggregates with exactly-once effect under crashes (simulated broker; Kafka/Redpanda not used) | Python | yes | 2 | local-variant | built |
| 38 | `spark-structured-sessionisation` | Sessionisation with Spark Structured Streaming and late data | PySpark 4.2 Structured Streaming | yes | 3 | full | built |
| 39 | `postgres-logical-cdc-to-lake` | Postgres logical-decoding CDC to a Parquet lake: snapshot plus stream, atomic transactions, crash-safe replay (real Postgres 16; Debezium and Kafka not used) | PostgreSQL 16 logical decoding (test_decoding), psycopg, DuckDB, PyArrow | partial | 2 | local-variant | built |
| 40 | `dbt-scd2-snapshots` | Star schema with SCD2 snapshots and metrics | dbt, DuckDB/Postgres | yes | 2 | full | built |
| 41 | `airflow-dag-factory` | Config-driven Airflow DAG factory with tests | Airflow 3.3.2, PyYAML | yes | 3 | full | built |
| 42 | `dagster-asset-pipeline` | Asset-based pipeline with partitions and checks | Dagster, DuckDB | yes | 3 | full | built |
| 43 | `spark-skew-optimisation` | Spark join-skew diagnosis and fixes with local before/after benchmarks | PySpark 4, AQE, local[*] | yes | 3 | full | built |
| 44 | `parquet-compaction-lab` | Parquet small-file compaction lab: pruning, sorting and codecs | PyArrow, DuckDB | yes | 3 | full | built |
| 45 | `incremental-api-ingestion` | Idempotent API ingestion with watermarks and backoff | Python, httpx, SQLite state | yes | 2 | full | built |
| 46 | `pit-feature-store` | Feature store offline/online with point-in-time joins (own implementation, Feast not used) | DuckDB, pandas, scikit-learn | yes | 3 | full | built |
| 47 | `idempotent-vector-ingestion` | Idempotent vector ingestion: re-embed only changed chunks | pgvector, hashing, Python | yes | 2 | full | built |
| 48 | `openlineage-lineage` | End-to-end lineage with OpenLineage (local receiver; Marquez/Airflow not used) | openlineage-python, stdlib HTTP | yes | 3 | local-variant | built |
| 49 | `reverse-etl-sync` | Reverse ETL to a mock CRM with diff-based sync | DuckDB, FastAPI mock CRM, SQLite | yes | 3 | full | built |
| 50 | `data-product-template` | Data-mesh data product template with contract and SLOs | YAML contracts, dbt | yes | 3 | full | built |

## AWS & Cloud

| # | POC | What it proves | Stack | Runs | Wave | Fidelity | Status |
|---|---|---|---|---|---|---|---|
| 51 | `terraform-aws-datalake-foundation` | Secure data-lake foundation (S3/KMS/IAM) in Terraform, checked with plan rules and a mock AWS | Terraform, moto (mock AWS), Python plan rules | yes | 1 | full | built |
| 52 | `glue-athena-iceberg` | Iceberg lakehouse: evolution, time travel and maintenance (PyIceberg local; Glue/Athena configured on a mock) | PyIceberg, Terraform, moto | aws | 2 | local-variant | built |
| 53 | `eventbridge-lambda-etl` | Event-driven ETL with EventBridge, SQS and a Lambda-style handler (local mock; Lambda/LocalStack not used) | moto, boto3, pyarrow | yes | 2 | local-variant | built |
| 54 | `step-functions-ml-pipeline` | ML pipeline orchestrated by a Step Functions-style state machine (own ASL interpreter; CDK/AWS not used) | Python ASL interpreter, scikit-learn, moto | aws | 3 | local-variant | built |
| 55 | `bedrock-rag-cdk` | Local variant: Bedrock knowledge-base RAG as CDK - offline synthesis, template checks and a local mirror pipeline (not deployed; no AWS calls) | CDK, Bedrock, OpenSearch Serverless | aws | 2 | local-variant | built |
| 56 | `endpoint-autoscaling-study` | Inference endpoint autoscaling policy study: a queueing simulation validated against Erlang C (SageMaker not used) | Python, NumPy | aws | 3 | local-variant | built |
| 57 | `emr-serverless-cost-tuning` | Spark cost/performance tuning study: real local Spark timings, modelled EMR Serverless price (nothing deployed to AWS) | PySpark, Python cost model, Terraform plan-only | aws | 3 | local-variant | built |
| 58 | `kinesis-firehose-ingest` | Streaming ingest via Kinesis and Firehose to S3 (local mock; Terraform/LocalStack not used) | moto, boto3 | yes | 3 | local-variant | built |
| 59 | `redshift-serverless-elt` | Local variant: Redshift Serverless ELT with dbt - executed on DuckDB with Redshift-dialect and Terraform plan checks (nothing run on Redshift or AWS) | Terraform, dbt | aws | 3 | local-variant | built |
| 60 | `lakeformation-access-control` | Fine-grained data access with Lake Formation semantics (simulated on DuckDB; no AWS service) | Python, sqlglot, DuckDB | aws | 3 | local-variant | built |
| 61 | `finops-cost-anomaly` | FinOps cost anomaly detection and tagging audit | CUR sample data, Python | yes | 2 | full | built |
| 62 | `landing-zone-guardrails` | Multi-account guardrails with SCPs and Config rules (simulated SCP evaluation; Terraform on local mock) | Terraform, moto, Python | partial | 3 | local-variant | built |
| 63 | `aws-mock-test-harness` | Reusable AWS integration-test harness (moto server; LocalStack/Docker not used) | pytest, moto, boto3 | yes | 2 | local-variant | built |
| 64 | `fargate-bluegreen-api` | ECS Fargate blue/green deploy with GitHub OIDC: security linting and deployment simulation (nothing deployed) | Terraform plan, Python, GitHub Actions | aws | 3 | local-variant | built |

## MLOps & LLMOps

| # | POC | What it proves | Stack | Runs | Wave | Fidelity | Status |
|---|---|---|---|---|---|---|---|
| 65 | `mlflow-registry-promotion` | Model registry promotion with gated checks | MLflow, GitHub Actions | yes | 2 | full | built |
| 66 | `drift-monitoring` | Data and prediction drift monitoring with alerts | Evidently, Prometheus | yes | 2 | full | built |
| 67 | `cicd-for-ml` | CI/CD for ML: tests data checks and reproducible builds | GitHub Actions, DVC, pytest | yes | 2 | full | built |
| 68 | `canary-model-serving` | Canary model rollout with automatic rollback (simulated traffic; KServe/kind not used) | Python, numpy, scipy, PyYAML | yes | 3 | local-variant | built |
| 69 | `shadow-ab-testing` | Shadow deployment and A/B test analysis | FastAPI, statsmodels | yes | 3 | full | built |
| 70 | `train-serve-skew-detector` | Training/serving skew detector | Python, Parquet | yes | 3 | full | built |
| 71 | `llm-tracing-otel` | LLM tracing and cost telemetry with OpenTelemetry | OpenTelemetry SDK (in-memory exporter) | yes | 2 | local-variant | built |
| 72 | `auto-retraining-trigger` | Drift-triggered automated retraining pipeline | Prefect/Airflow, MLflow | yes | 3 | full | built |
| 73 | `model-card-generator` | Auto-generated model cards from run metadata | MLflow, Jinja | yes | 3 | full | built |
| 74 | `batch-inference-scheduler` | Cost-aware batch inference scheduler | Python, queue, cost model | yes | 3 | full | built |
| 75 | `branching-data-store` | Data versioning with branching (own lakeFS/DVC-style store; lakeFS/DVC not used) | Python stdlib | yes | 3 | local-variant | built |
| 76 | `reproducibility-kit` | Experiment reproducibility kit (seed env data hash) | Python, subprocess, hashlib (uv/DVC not run) | yes | 3 | full | built |

## Governance & Security

| # | POC | What it proves | Stack | Runs | Wave | Fidelity | Status |
|---|---|---|---|---|---|---|---|
| 77 | `data-contracts-ci-gate` | Data contracts enforced as a CI gate with breaking-change detection | YAML, Great Expectations, GitHub Actions | yes | 1 | full | built |
| 78 | `pii-scanner-classifier` | PII discovery and classification scanner for tables/files | Python, DuckDB | yes | 2 | full | built |
| 79 | `prompt-injection-red-team` | Prompt-injection evaluation against the triage agent and MCP server, with and without defences | Python, synthetic attack corpus, canary scoring | yes | 1 | full | built |
| 80 | `ai-risk-register-generator` | AI risk register generator aligned to EU AI Act categories | Python, Jinja2, PyYAML | yes | 3 | full | built |
| 81 | `fairness-audit` | Model bias and fairness audit report | Fairlearn, scikit-learn | yes | 3 | full | built |
| 82 | `lineage-impact-analysis` | Column-level lineage and change impact analysis | sqlglot, networkx | yes | 2 | full | built |
| 83 | `policy-as-code-opa` | RBAC/ABAC data access policy-as-code | OPA/Rego, Python, pytest | yes | 3 | full | built |
| 84 | `tamper-evident-audit-log` | Tamper-evident audit log with hash chaining | Python, SQLite | yes | 3 | full | built |
| 85 | `synthetic-data-privacy` | Synthetic data generation with privacy metrics | Python, NumPy, SciPy, scikit-learn | yes | 3 | full | built |
| 86 | `secrets-scanning-kit` | Pre-commit and CI secrets scanning kit | Python, gitleaks, git hooks | yes | 2 | full | built |
| 87 | `llm-output-safety-filter` | Layered LLM output safety filter with eval | Python, scikit-learn, pattern layers | yes | 3 | full | built |
| 88 | `gdpr-erasure-pipeline` | GDPR erasure and retention pipeline for lake + vector store | Delta Lake (delta-rs), Python, Fernet | yes | 2 | full | built |

## ML & Analytics

| # | POC | What it proves | Stack | Runs | Wave | Fidelity | Status |
|---|---|---|---|---|---|---|---|
| 89 | `timeseries-forecast-benchmark` | Forecasting benchmark: classical vs ML vs foundation models | Python, statsmodels, LightGBM | yes | 3 | full | built |
| 90 | `streaming-anomaly-detection` | Streaming anomaly detection on metrics | Python, river | yes | 3 | full | built |
| 91 | `churn-uplift-modeling` | Churn and uplift modelling with decision thresholds | Python, scikit-learn (own uplift learners) | yes | 3 | full | built |
| 92 | `two-tower-recommender` | Two-tower recommender with offline evaluation (NumPy, FAISS; PyTorch not used) | NumPy, FAISS, scikit-learn | partial | 3 | local-variant | built |
| 93 | `causal-diff-in-diff` | Causal impact via diff-in-diff and synthetic control | statsmodels, pandas | yes | 3 | full | built |
| 94 | `sentiment-slm-vs-classical` | Sentiment: SLM vs classical baseline cost/accuracy | scikit-learn, Ollama | yes | 3 | local-variant | built |
| 95 | `entity-resolution` | Entity resolution and deduplication at scale | Splink 5, DuckDB | yes | 3 | full | built |
| 96 | `hierarchical-forecast-reconciliation` | Hierarchical demand forecasting with reconciliation | Python, statsmodels (own reconciliation) | yes | 3 | full | built |

## Capstone

| # | POC | What it proves | Stack | Runs | Wave | Fidelity | Status |
|---|---|---|---|---|---|---|---|
| 97 | `reference-ai-data-platform` | Reference AI data platform: lakehouse, contract gate, incremental vector index, model gate and tracing wired end to end (file-batch source; no Debezium) | DuckDB, MLflow, OpenTelemetry, SQLite; composes POCs 035/047/065/071/077 | partial | 4 | local-variant | built |
| 98 | `reference-enterprise-rag` | Reference architecture: governed enterprise RAG platform | Python (composes POCs 001,002,006,034,078,084,088) | partial | 4 | full | built |
| 99 | `reference-edge-cloud-slm` | Edge-to-cloud hybrid serving reference (int8 ONNX edge model, confidence escalation, drift monitor, circuit breaker; cloud modelled, no language model) | ONNX Runtime, scikit-learn; composes POCs 017 and 066 | partial | 4 | local-variant | built |
| 100 | `poc-portfolio-site` | Auto-generated portfolio site and POC scaffolding CLI | Python, markdown-it-py | yes | 4 | full | built |
