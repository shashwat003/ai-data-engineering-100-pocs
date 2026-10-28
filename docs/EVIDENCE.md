# Evidence ledger

Every claim in this repository belongs to exactly one class. If it is not in the first table, it is
not claimed.

| Class | Meaning | How to reproduce |
|---|---|---|
| **Measured (deterministic)** | Produced by running code in this repo on seeded synthetic data; identical across runs on one machine; see the exception below | `make demo` |
| **Simulated** | Produced by a model of a system with assumed parameters; shows a mechanism, not reality | `make demo` (POC 005 default) |
| **Measured on real models** | Produced by running real local models | **None exist yet** |
| **Implemented, not measured** | Code and tests exist (using fakes/stubs) but it has never run against the real thing | see commands in each POC README |

## Current inventory

| Item | Class |
|---|---|
| POC 001: BM25 vs LSA dense vs LSA hybrids, metrics, paired comparisons, ablations | Measured (deterministic), synthetic corpus |
| POC 001: neural embedding retriever (`--neural sentence-transformers` / `ollama`) | Implemented, not measured |
| POC 005: router policies, thresholds, budget guard, confidence sensitivity | Simulated |
| POC 005: `--backend ollama` with two configurable local models | Implemented, not measured (tested against a stub server) |
| POC 003: execution accuracy, attack-suite containment, false positives (rule planner, synthetic warehouse) | Measured (deterministic); questions author-written |
| POC 003: LLM planner / LLM SQL writer (`--planner llm`, `llm-sql`) | Implemented, not measured (stub model only) |
| POC 024: MCP server behaviour, security checks, scripted session | Measured (tests incl. real subprocess over stdio) |
| POC 024: interoperability | Tested with the official MCP Python client (`mcp==2.3.0`); no MCP host application tested |
| POC 077: defect detection (8 injected defect types), evolution policy | Measured / tested; synthetic feeds |
| POC 077: `data-contracts.yml` pull-request workflow | Written, not yet exercised on a real PR |
| POC 035: quarantine = injected defects exactly; replay no-op; incremental = full rebuild; SCD2 and gates | Measured / tested (DuckDB, synthetic data from the shared warehouse) |
| POC 035: throughput on 1M synthetic rows | Measured locally (3 runs, wall-clock, one machine); no cluster or object-store claims |
| POC 023: 10 scripted incidents, policy matrix, approval binding/expiry, audit tamper detection | Measured / tested (rule planner; scripted on-call) |
| POC 023: `LLMPlanner` with a real model | Implemented, not measured (stub model only) |
| POC 079: attack success rate per control (416 synthetic cases, two human models, leave-one-out) | Measured (deterministic) against a **worst-case compliant stand-in**, not an LLM |
| POC 079: susceptibility of any real LLM to injection | **Not measured** |
| POC 051: fmt/validate/plan rules; real `apply` of the module to a **moto mock**; re-check of applied state | Tested (Terraform 1.9.8, AWS provider 5.82.2) |
| POC 051: policy enforcement by AWS, Budgets resource, behaviour and cost on real AWS | **Not tested** (moto does not enforce conditions; Budgets unsupported; nothing provisioned) |
| POC 043: join-skew diagnosis and 5 strategies, before/after wall-clock and task stats | Measured locally (PySpark 4.2.0, `local[4]`, 4M synthetic rows, 5 timed runs each); not cluster results |
| Any real cost saving | Not claimed |
| Retrieval or routing quality on real data | Not tested |

## Verification record (2026-10-19)

| Check | Result |
|---|---|
| Fresh `git clone` of the pushed branch, new venv, `pip install -r requirements.txt` + dev tools, `make check`, `make demo` | Passed (before pinning: demo output differed only in the recorded numpy version string, metrics identical) |
| Same on a clean working-tree copy with pinned `requirements-dev.txt`, Python 3.12 and 3.13 | Lint, format, all tests, demos passed; regenerated results identical to the committed ones apart from version strings |
| Python 3.11 | **Unsupported**: `numpy==2.5.4` has no installable build for 3.11 (resolver reports unsatisfiable), so the project requires 3.12+ |
| GitHub Actions run #1 (commit `f3e5eda`) | Success |
| GitHub Actions run #2 (commit `a1339f3`), jobs `check (3.12)` and `check (3.13)`: pip install of pinned dev requirements, lint, tests, secret scan, all demos, catalogue check | Success (both jobs) |
| Test coverage (line+branch, measured locally once, not enforced in CI) | libs 94%, POC 001 97%, POC 005 98% |
| GitHub Actions run #3 (`baee3c0`) and run #4 (`36a3a91`, adds POCs 003/024/077 and the MCP interop step) | Success |
| Fresh clone of `36a3a91`, new virtualenvs on Python 3.12 and 3.13: lint, format, all tests, secret scan, all demos; official-MCP-client interop test on 3.13 | Passed (3.12: 43 passed in POC 024 with interop skipped; 3.13 with interop installed: 44 passed) |
| GitHub Actions run #5 (`3d3f530`) | Success |
| Secret scan, working tree and full git history (regex patterns) | Clean. The scanner was also checked to fail on a planted fake AWS key id |
| Hugging Face, Ollama registry reachable from the dev sandbox | **No** (proxy 403/refused), hence no neural or real-model results |

CI results for later commits are visible under the repository's Actions tab; they are not recorded
here because this file is written before they run.

## Known exception: trained-model results are platform-sensitive
Results that depend on a freshly trained scikit-learn model (for example POC 014's quantisation study, POC 099's edge/cloud accuracies) can differ across CPUs and BLAS builds even with fixed seeds: a GitHub runner on Python 3.13 trained a toy model where 2-bit quantisation looked *better* than 4-bit, while the reference run showed +18% perplexity. The tests of such POCs assert only platform-independent facts (orderings that hold by construction, arithmetic, invariants), and the numbers in their READMEs are labelled as one run on one machine.

## Fidelity classes (catalogue column `fidelity`)
- `full`: implemented as briefed; the named open-source components run locally (for example DuckDB, MLflow, Spark, dbt, Terraform plan/apply against a local mock). 71 POCs.
- `local-variant`: a service, model or hardware from the brief is replaced by a local or simulated stand-in, and the POC title says so (29 POCs: 10, 14, 15, 16, 19, 20, 21, 37, 39, 48, 52, 53, 54, 55, 56, 57, 58, 59, 60, 62, 63, 64, 68, 71, 75, 92, 94, 97, 99). Variants 15, 19, 55 and 59 were approved as scope adjustments of the original briefs; each README states the original objective, what is covered and what is NOT verified.
- No POC claims a real transformer, AWS Bedrock/Redshift/EMR/Lambda, Kafka/Debezium or GPU result unless its evidence table says it was run; nothing in this repository was run on AWS.
