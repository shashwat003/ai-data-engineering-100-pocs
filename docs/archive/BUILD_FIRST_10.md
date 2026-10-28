> **Archived planning note.** Written before or during the build; kept for history, not part of the portfolio and possibly out of date. See the repository README and docs/STATUS.md for the current state.

# The 10 POCs to Build First (Wave 1)

Selection criteria: hiring-manager signal, covers every pillar, runs locally with no credentials, and builds reusable `libs/` that speed up later POCs.

| Order | # | POC | Why it impresses | Reusable output |
|---|---|---|---|---|
| 1 | 1 | `rag-hybrid-search-eval` | Most RAG demos skip evaluation; this measures recall/MRR/groundedness with ablations | `eval_kit`, `llm_client` |
| 2 | 5 | `llm-router-cost-optimiser` | Shows cost/latency engineering with real numbers (SLM-first cascade) | cost ledger, router |
| 3 | 3 | `text-to-sql-semantic-layer` | High-demand enterprise use case with SQL validation and guardrails | sqlglot validator |
| 4 | 24 | `mcp-server-data-catalog` | Current, in-demand agent-tooling pattern; read-only by design | MCP scaffold |
| 5 | 23 | `langgraph-incident-triage-agent` | Agent with tools, human approval and traceable decisions | approval gate |
| 6 | 35 | `medallion-lakehouse-duckdb` | Core data-engineering credibility: layers, tests, docs, idempotency | `data_gen`, dbt template |
| 7 | 77 | `data-contracts-ci-gate` | Governance as code that fails the build on breaking changes | contract checker |
| 8 | 79 | `prompt-injection-red-team` | Security depth most portfolios lack; scored attack suite | attack corpus |
| 9 | 15 | `qlora-ticket-classifier` | SLM fine-tuning with before/after metrics (needs a GPU or Colab; CPU tiny-model fallback) | training template |
| 10 | 51 | `terraform-aws-datalake-foundation` | AWS architecture with least-privilege IAM, KMS, tested on LocalStack at zero cost | `infra/modules`, `testing` |

Suggested order of work: Wave 0 (`libs/`, CI, landing README) then 1 -> 2 -> 3 (shared eval and LLM client), then 6 -> 7, then 4 -> 5 -> 8, then 10 and 9.

Deferred on purpose: Bedrock RAG (#55) and other `aws` POCs need your AWS account and budget approval first.
