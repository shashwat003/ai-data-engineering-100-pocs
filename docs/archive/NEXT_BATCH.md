> **Archived planning note.** Written before or during the build; kept for history, not part of the portfolio and possibly out of date. See the repository README and docs/STATUS.md for the current state.

# Next batch (not started)

Batch 2 (POCs 035, 023, 079, 051, 043) is built; see the catalogue. Recommended next five, chosen for
what they add and because each can be **verified in the development environment**:

| # | POC | Why now | Builds on | How it will be verified |
|---|---|---|---|---|
| 28 | `agent-trajectory-eval` | Turns the ad-hoc scoring in POC 023 into a reusable trajectory-evaluation harness (tool-order, forbidden-call and cost metrics) | #23, #79 | Scripted trajectories with known verdicts; metrics must match hand-computed values |
| 36 | `delta-lake-cdc-merge` | Moves from the SQL upsert in POC 035 to a real table format with `MERGE`, time travel and compaction | #35 | `delta-rs`/Spark Delta locally; row-level equality with the DuckDB result |
| 66 | `drift-monitoring` | MLOps coverage: PSI/KS drift detectors with injected, known drift | `data_gen` | Detection rate and false-alarm rate on seeded drift, exact |
| 7 | `prompt-regression-testing` | LLM CI: golden-set regression gate with statistical tolerance, runnable with a stub model | `llm_client`, `eval_kit` | Injected regressions must fail the gate; unchanged prompts must pass |
| 61 | `finops-cost-anomaly` | Cloud-cost signal without an account: anomaly detection on synthetic cost-and-usage data | #51 (budget guard) | Injected cost spikes recovered at known dates; precision/recall reported |

Deferred: anything needing GPUs, Hugging Face downloads (#15, #14) or a paid AWS service (#52, #55, #59).
