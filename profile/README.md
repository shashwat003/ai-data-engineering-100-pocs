<!--
  Draft of the special profile README for shashwat003/shashwat003. NOT published.
  Only details supplied by the owner are used: name, positioning, 10+ years, Dublin, and the listed skills.
  Add contact links, current employer or certifications yourself if you want them; none are assumed here.
-->
# Shashwat

**Senior Data Engineering & AI Engineering Professional** · 10+ years · Dublin, Ireland

I design and build data platforms and AI systems that have to work in production: reliable pipelines, well-governed data, and LLM, small-model and agentic applications that are tested, observable and honest about what they do not know.

## Technical expertise
| Area | Focus |
|---|---|
| Data architecture | lakehouse and warehouse design, data contracts, quality gates, lineage, distributed systems |
| Data engineering | Python, SQL, Spark, batch and streaming pipelines, orchestration |
| AI engineering | LLM applications, retrieval and grounding, evaluation, small language models, agentic systems |
| Cloud | AWS and cloud platform architecture, infrastructure as code, cost-aware design |

## The 100-POC portfolio
[**100 Real-World AI & Data Engineering POCs**](https://github.com/shashwat003/100-real-world-ai-data-engineering-pocs) is a body of work I built to show how I approach these problems: one production problem per proof-of-concept, solved in code, covered by automated tests, with a diagram, reproducible results and an explicit statement of what was and was not verified.

- **71 full-scope POCs** run the named open-source components locally; **29 approved local variants** replace a GPU, a hosted model or an AWS service with a labelled local stand-in and make no claims about the real service.
- More than 1,900 automated tests across the portfolio, no paid services and no credentials required; documentation is generated and checked in CI.
- Nothing in it was run on AWS or a GPU; the README of each POC says which parts are real, simulated, mocked or untested.

### Featured projects
| Project | What it shows |
|---|---|
| [098 · Governed enterprise RAG](https://github.com/shashwat003/100-real-world-ai-data-engineering-pocs/tree/main/pocs/098-reference-enterprise-rag) | retrieval with enforced citations, PII handling, audit log and erasure, composed from earlier POCs |
| [097 · AI data platform](https://github.com/shashwat003/100-real-world-ai-data-engineering-pocs/tree/main/pocs/097-reference-ai-data-platform) | lakehouse with a contract gate, incremental vector index, model-promotion gate and tracing; replay and tamper tests (local variant: file batches instead of CDC) |
| [039 · Postgres logical-decoding CDC](https://github.com/shashwat003/100-real-world-ai-data-engineering-pocs/tree/main/pocs/039-postgres-logical-cdc-to-lake) | snapshot-plus-stream handoff, atomic transactions and crash-safe replay against a real local PostgreSQL 16 (local variant: no Debezium or Kafka) |
| [035 · Medallion lakehouse](https://github.com/shashwat003/100-real-world-ai-data-engineering-pocs/tree/main/pocs/035-medallion-lakehouse-duckdb) | incremental, idempotent bronze/silver/gold with SCD2, quarantine and a data-contract gate |
| [079 · Prompt-injection red team](https://github.com/shashwat003/100-real-world-ai-data-engineering-pocs/tree/main/pocs/079-prompt-injection-red-team) | measurable attack success rates against layered defences |
| [003 · Governed natural-language-to-SQL](https://github.com/shashwat003/100-real-world-ai-data-engineering-pocs/tree/main/pocs/003-text-to-sql-semantic-layer) | a semantic layer with query validation and guardrails |

Other flagships: sandboxing agent-generated code (027), statistical model promotion (065), Terraform validated against a local AWS mock (051) and data contracts as a CI gate (077).

## Current engineering interests
LLM and small-language-model systems, agentic AI with policy gates and evaluation, data platforms for AI workloads, and making cloud and AI designs testable without paying for them.
