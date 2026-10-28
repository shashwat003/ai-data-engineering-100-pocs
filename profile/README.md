<div align="center">

# Shashwat

**Senior Data Engineering & AI Engineering Professional**

12+ years · Dublin, Ireland

[Portfolio: 100 AI & Data Engineering POCs](https://github.com/shashwat003/ai-data-engineering-100-pocs) · [Catalogue](https://github.com/shashwat003/ai-data-engineering-100-pocs/blob/main/docs/CATALOGUE.md) · [Evidence notes](https://github.com/shashwat003/ai-data-engineering-100-pocs/blob/main/docs/EVIDENCE.md)

</div>

---

## About

I design and build data platforms and AI systems that have to work in production: reliable pipelines, well-governed data, and LLM, small-model and agentic applications that are tested, observable and honest about what they do not know.

My work sits where data architecture meets AI engineering: getting trustworthy data to models, putting guardrails and evaluation around them, and keeping cloud designs cost-aware and testable.

## Technology

| | |
|---|---|
| **Languages** | Python · SQL |
| **Data engineering** | Spark · lakehouse and warehouse design · batch and streaming pipelines · orchestration · data contracts and quality gates |
| **AI engineering** | LLM applications · retrieval and grounding · evaluation · small language models · agentic systems |
| **Cloud & infrastructure** | AWS · cloud platform architecture · infrastructure as code · distributed systems |

## Featured: 100 Real-World AI & Data Engineering POCs

[**ai-data-engineering-100-pocs**](https://github.com/shashwat003/ai-data-engineering-100-pocs) is a body of work built to show how I approach these problems: one production problem per proof-of-concept, solved in code, covered by automated tests, with a diagram, reproducible results and an explicit statement of what was and was not verified.

| | |
|---|---|
| **71** | full-scope POCs that run the named open-source components locally |
| **29** | documented local variants that replace a GPU, a hosted model or an AWS service with a labelled local stand-in, and make no claims about the real service |
| **0** | paid services or credentials required |

Coverage: LLM & RAG · small language models · agents & automation · data engineering · AWS & cloud · MLOps & LLMOps · governance & security · ML & analytics · capstone reference architectures.

## Six standout projects

| Project | What it shows |
|---|---|
| [**098 · Governed enterprise RAG**](https://github.com/shashwat003/ai-data-engineering-100-pocs/tree/main/pocs/098-reference-enterprise-rag) | Retrieval with enforced citations, PII handling, audit log and subject erasure, composed from earlier POCs. |
| [**097 · AI data platform**](https://github.com/shashwat003/ai-data-engineering-100-pocs/tree/main/pocs/097-reference-ai-data-platform) | Lakehouse with a contract gate, incremental vector index, model-promotion gate and tracing, with replay and tamper tests. Local variant: file batches instead of CDC. |
| [**039 · Postgres logical-decoding CDC**](https://github.com/shashwat003/ai-data-engineering-100-pocs/tree/main/pocs/039-postgres-logical-cdc-to-lake) | Snapshot-and-stream handoff, atomic transactions and crash-safe replay against a real local PostgreSQL 16. Local variant: no Debezium or Kafka. |
| [**035 · Medallion lakehouse**](https://github.com/shashwat003/ai-data-engineering-100-pocs/tree/main/pocs/035-medallion-lakehouse-duckdb) | Incremental, idempotent bronze/silver/gold layers with SCD2, quarantine of bad records and a data-contract gate. |
| [**079 · Prompt-injection red team**](https://github.com/shashwat003/ai-data-engineering-100-pocs/tree/main/pocs/079-prompt-injection-red-team) | Measured attack success rates against layered defences. |
| [**003 · Governed natural-language-to-SQL**](https://github.com/shashwat003/ai-data-engineering-100-pocs/tree/main/pocs/003-text-to-sql-semantic-layer) | A semantic layer with query validation and guardrails, backed by an extensive guard test suite. |

Also worth a look: [agent code sandbox (027)](https://github.com/shashwat003/ai-data-engineering-100-pocs/tree/main/pocs/027-agent-code-sandbox) · [statistical model promotion (065)](https://github.com/shashwat003/ai-data-engineering-100-pocs/tree/main/pocs/065-mlflow-registry-promotion) · [Terraform against a local AWS mock (051)](https://github.com/shashwat003/ai-data-engineering-100-pocs/tree/main/pocs/051-terraform-aws-datalake-foundation) · [data contracts as a CI gate (077)](https://github.com/shashwat003/ai-data-engineering-100-pocs/tree/main/pocs/077-data-contracts-ci-gate)

> Each POC's README states which parts are real, simulated, mocked or untested. Nothing in the portfolio was run on AWS or a GPU.

## Current interests

- LLM and small-language-model systems, and when a small model is enough
- Agentic AI with policy gates, sandboxing and evaluation
- Data platforms built for AI workloads: contracts, lineage, vector indexes
- Making cloud and AI designs testable without paying for them

---

<div align="center"><sub>Dublin, Ireland</sub></div>
