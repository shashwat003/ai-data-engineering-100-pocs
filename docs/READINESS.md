# Readiness checklist (publication review)

State on branch `claude/relaxed-allen-usvr0o` (private repository; nothing merged, nothing public, no cloud resource ever created).

## A. Per-POC acceptance (all 100)
| Requirement | Status | How it is checked |
|---|---|---|
| Functional implementation | met | tests run the real code paths; heavy POCs run in `heavy.yml` |
| Distinct real-world problem | met | one problem per README; catalogue titles are unique (CI) |
| Reproducible demo | met | `make demo` per POC; light demos smoke-run in fast CI, heavy demos in `heavy.yml` |
| README with Mermaid and limitations | met | enforced by the repo-wide check of POC 100 (0 problems) |
| Automated tests | met | `scripts/run_tests.py`; totals in section K |
| Synthetic / licensed data only | met | all data seeded synthetic; secret scan clean |
| Error handling and secure defaults | met, reviewed per POC | fail-closed parsing/gates, validation, mock credentials; not independently audited |
| Evidence of execution | met | `results/` embedded in each README |
| Honest labelling | met | *Evidence status* table per POC; `fidelity` column (71 `full`, 29 `local-variant`) in [STATUS.md](STATUS.md) |

## B. Deliverables
README, [FLAGSHIPS.md](FLAGSHIPS.md), [STATUS.md](STATUS.md), [ARCHITECTURE.md](ARCHITECTURE.md), [DEMO.md](DEMO.md), [CI_REPORT.md](CI_REPORT.md), [EVIDENCE.md](EVIDENCE.md), [RELEASE_SUMMARY.md](RELEASE_SUMMARY.md), this file, `LICENSE` / `NOTICE` / [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) (drafts for review). A static searchable site can be generated with `cd pocs/100-poc-portfolio-site && PYTHONPATH=.:../../libs python -m poc_portfolio_site.cli --root ../.. build --out site` (97+ pages, no external requests; not published).

## C. Approved scope adjustments (POCs 15, 19, 55, 59)
Approved as local, educational variants. Each README opens with *Original brief and what this local variant covers* and each catalogue title starts with "Local variant:".
| # | Original brief | Implemented and tested | Not verified |
|---|---|---|---|
| 15 | QLoRA fine-tune of a small model | NumPy QLoRA: frozen Q4_0 base, adapters, gradient checks, merge | transformers, PEFT, bitsandbytes, GPU, MLflow |
| 19 | Fine-tune a small model for tool calling | LoRA router + tagger + strict JSON-Schema gate, mutation tests | any language model generating calls |
| 55 | Bedrock KB RAG deployed with CDK | CDK synthesised offline, cfn-lint, policy checks, local mirror, botocore request validation | deployment, Bedrock, Titan, OpenSearch Serverless |
| 59 | Redshift Serverless ELT with dbt | dbt on DuckDB, point-in-time SCD2 dimension, local COPY semantics, Redshift-dialect syntax checks, Terraform plan checks | Redshift, dbt-redshift, COPY from S3 |

**POC 59 correction (requested before approval).** Rule chosen: *historical point-in-time* segmentation, because revenue must be attributed to the segment that placed the order; today's segmentation is a separate report (`revenue_by_current_segment`). Fixes: SCD2 `dim_customer_history` from dated snapshots (identical consecutive snapshots collapse; validity from effective dates, never from load order); `fct_orders` joins by order date and its incremental filter also re-processes every order of a customer whose history changed; re-sent order versions are ranked by batch sequence, not arrival time. Regression tests (36 in total for the POC) cover changed segments (no retroactive change), orders before the first snapshot, late-arriving orders dated in an earlier window, late-arriving customer snapshots, repeated loads and builds, SCD2 row-count arithmetic, and incremental == full refresh (all columns) for four file-arrival orders; results are reconciled against an independent DuckDB ASOF-join computation (0 mismatching orders of 998).

## D. Known limitations and risks
- Local variants show engineering mechanisms, not behaviour of the replaced service or model; do not quote their numbers as Bedrock, Redshift, EMR or transformer results.
- Trained-model results are platform-sensitive (POCs 014, 099 and others); tests assert platform-independent facts only.
- Single seeds in most studies; timings measured once and never asserted.
- Heavy CI is path-filtered; run `python scripts/run_tests.py --heavy` after shared-library changes.
- Moto limits: Lambda invocation needs Docker; Step Functions definitions are not validated by moto (POC 054 uses its own interpreter).
- GitHub Actions emit Node 20 deprecation warnings (`checkout@v4`, `setup-python@v5`, ...).
- Git history contains 10 wheel files (1.8 MB, byte-identical to PyPI releases) and 2 dbt logs (sandbox paths only); removed from the tree, not purged. The pack is about 0.2 MB.

## E. Needs the repository owner (nothing below was done)
Merge to `main`, change visibility, enable branch protection, confirm the licence holder name, publish the profile README or site, any cloud deployment, history rewrite.

## G. Security review
- **Secrets:** `scripts/check_secrets.py` clean. Full-history **gitleaks 8.30.0** scan (76+ commits): 4 findings, all false positives, now listed by exact fingerprint with reasons in `.gitleaksignore` (a test canary and a fake key assembled from two literals in POC 023; two lists of policy rule ids in POC 051). With that file the history scan reports **no leaks**. A working-tree scan additionally flags untracked `__pycache__/*.pyc` copies of test fixtures (not in git).
- **Dependency audit (`pip-audit`):** full log, classification and mitigations in [DEPENDENCY_AUDIT.md](DEPENDENCY_AUDIT.md). The Airflow environment was upgraded to 3.3.2 with PyJWT/urllib3 overrides (162 advisory rows / 102 unique -> **0**; POC 041 tests 23 passed on 3.1.8 and 3.3.2). The main environment has **1 accepted advisory**: `diskcache 5.6.3` (CVE-2025-69872, no fixed release, transitive via DVC, POCs 067/076 only; usage restriction documented). CVSS data was unavailable offline, so severity is not asserted.
- Infrastructure code uses literal `mock` credentials or a placeholder account id and was never applied outside a local mock; the PostgreSQL test server listens on a unix socket only.

## H. Licensing
Apache-2.0 prepared for review: `LICENSE` (canonical text from apache.org), `NOTICE` (copyright holder line to be confirmed) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) (55 directly pinned packages with licences, plus every copyleft-licensed package in the full environment: psycopg LGPL-3.0; the DVC chain with GPL-2.0 `grandalf`, `pygit2` and dual-licensed `dulwich`/`asyncssh`; MPL-2.0 `certifi`, `pathspec`, `tqdm`, `orjson`, `shtab`). Nothing is vendored or redistributed. Not published and not applied to the repository's visibility.

## I. Flagships and pins
Pin: **098**, **097**, **039**, **035**, **079**, **003**; keep **027**, **065**, **051**, **077** as the other flagships ([FLAGSHIPS.md](FLAGSHIPS.md), which also shows each flagship's fidelity).

## J. Steps to publish
See [PUBLISHING_CHECKLIST.md](PUBLISHING_CHECKLIST.md) (owner-gated steps marked).

## K. Verification totals
| Suite (local, Python 3.13, final tree) | Suites | Passed | Failed | Skipped |
|---|---|---|---|---|
| `run_tests.py --fast` (includes `libs/tests`) | 81 | 1,499 | 0 | 4 |
| `run_tests.py --heavy` (Spark, Terraform/moto, dbt, Dagster, Playwright, PostgreSQL, CDK, OPA, ...) | 28 | 483 | 0 | 0 |
| **Total** | **109** | **1,982** | **0** | **4** |

The four skips: POC 003 (1, a case documented as enforced by the validator only), POC 027 (1, a sandbox layer that needs privileges), POC 086 (2, need the gitleaks binary, which CI installs and which was run separately here). The Airflow POC (041) runs only in the CI `airflow` job (green). Static count of test functions: 1,364 (parametrised tests expand to more cases when run). GitHub Actions: see [CI_REPORT.md](CI_REPORT.md).
