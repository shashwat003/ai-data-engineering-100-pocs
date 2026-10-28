# Test and CI report

## Workflows
| Workflow | Trigger | What it runs |
|---|---|---|
| `ci.yml` (fast) | every push / PR; matrix Python 3.12 and 3.13 | `make lint` (`ruff check .` and `ruff format --check .`), `python scripts/run_tests.py --fast` (each POC's tests in its own process, plus `libs/tests`), `make secrets` (regex secret scan), MCP interoperability test with the official client, light demo smoke (`run_demos.py --fast`), catalogue/status/flagships freshness (`git diff --exit-code` after regeneration) |
| `heavy.yml` | path-filtered to heavy POCs and marker files | job `heavy`: Java, Terraform 1.9.8, OPA, Playwright Chromium, PostgreSQL 16 binaries; `run_tests.py --heavy`, then `run_demos.py --heavy` (Spark and PostgreSQL in `DEMO_QUICK` mode). Job `airflow`: the exact lock `requirements-airflow-lock.txt` (Airflow 3.3.2, official constraints plus PyJWT/urllib3 security overrides) in its own environment, `--own-env` tests and demos |
| `data-contracts.yml` | pull requests touching `pocs/077-*/contracts/**` | POC 077's gate against the base branch: blocks a contract change without the matching version bump, a deleted contract or a broken contract format |
| `ml-pipeline.yml` | push / PR touching `pocs/067-cicd-for-ml/**` | POC 067: reproduces the DVC pipeline and enforces the data and model gates |

All third-party installs in CI are pinned: Python requirements are exact pins; gitleaks and OPA are downloaded with a pinned version and verified sha256.

## Test inventory (this commit)
- Built POCs: 96, each with tests; static count of test functions: see [STATUS.md](STATUS.md) (1,282 across POCs; parametrised tests expand to more cases when run, e.g. POC 003 runs 131 cases from 26 functions). Shared libraries are tested separately in `libs/tests` (20 tests).
- Heavy-tier POCs (20, listed in [DEMO.md](DEMO.md)) and the Airflow POC run only in `heavy.yml`; the other 75 run in fast CI (two of them, 016 and 079, only run their demos in the heavy tier).
- Tests never assert timings. Tests of trained toy models assert platform-independent facts only (see below).
- The repo-wide consistency check (POC 100) is itself a test: every built POC must have tests, the required README sections, a Mermaid diagram, listed limitations and a results file. It reports 0 problems.

## Local verification (sandbox, Python 3.13)
`python scripts/run_tests.py --fast` on the final tree: **ALL TEST SUITES PASSED** (81 suites, 1499 test cases passed, 4 skipped).

## CI history observed in the final stretch (runs 21-44)
| Runs | Result | Cause and fix |
|---|---|---|
| fast #21-28 | green | |
| fast #29-31 | red at `make lint` | `ruff format --check` failures on newly added files (CI runs `ruff check` **and** `ruff format --check`); fixed, and both commands added to the pre-commit routine |
| fast #32-36 | green | batches 16-18 |
| fast #37 | 3.13 job red, 3.12 green | POC 014's end-to-end test asserted magnitudes from a trained toy MLP (2-bit quantisation > +5% perplexity); the 3.13 runner trained a model where it was +0.24% |
| fast #38-41 | 3.13 job red | the same test (confirmed from logs for #37 and #41); the first fix still asserted an ordering that the runner's model violated (Q2S better than Q4_0). Final fix `54d866c`: assert only platform-independent facts |
| heavy #7-13 | green except #10, #11 | #10, #11: POC 063 asserted a Lambda invocation result that needs Docker (present on GitHub runners, absent in the sandbox) and 048 had a flaky oversize-body assertion; both fixed in `37c1369`, heavy #12 green |
| heavy #14, #15 | tests green; demo step see below | POC 057 (Spark cost tuning) added to path filters |


## Final verification (commit `4a46c14`, pushed after the pending-approval variants)
| Check | Result |
|---|---|
| Fast CI `ci` #47 (py3.12 + py3.13: lint, fast tests, secrets, MCP interop, demo smoke, catalogue/status freshness) | success |
| Heavy `heavy-validation` #18, job `airflow` | success |
| Heavy `heavy-validation` #18, job `heavy`: install (incl. CDK, Node 22), `run_tests.py --heavy` (28 suites, includes the four new POCs) | success |
| Local `run_tests.py --fast` (final tree before the variants) | 81 suites, 1,499 passed, 4 skipped, 0 failed |
| Local `run_tests.py --heavy` | 28 suites, 474 passed, 0 failed (083 needs the OPA binary on PATH: skipped until supplied, then 8 passed) |
| Local new variants | 015: 18, 019: 46, 055: 23, 059: 27 passed |

## Lessons that changed the process
1. Run `ruff check .` and `ruff format --check .` with the pinned ruff before every commit.
2. Trained models are not portable across CPUs/BLAS: assert invariants, not magnitudes.
3. Anything that needs Docker, a GPU or a daemon must be conditional or in the heavy tier, and the README must say what was not exercised.
4. Reproduce CI failures locally in the same privilege mode (`setpriv --reuid=65534 ...`) because root and non-root behave differently for sandboxing and PostgreSQL.
5. Keep job-log requests small; never paginate workflow runs with the GitHub tool's default output.

## Release candidate verification (approval round)
| Check | Result |
|---|---|
| Local `run_tests.py --fast` | 81 suites, 1,499 passed, 0 failed, 4 skipped |
| Local `run_tests.py --heavy` (incl. POCs 15, 19, 55, 59; POC 59 has 36 tests) | 28 suites, 483 passed, 0 failed |
| Fast CI `ci` #49 (`2134025`), #51 (`92ce916`) | success (#50 failed on interim commit `383c5c5` because generated docs were out of date; fixed by `92ce916`) |
| Heavy `heavy-validation` #19 (`2134025`, includes the point-in-time POC 59 tests) | success |
| Final commit | see the release report; results for the last push are recorded in `PROGRESS.md` |
