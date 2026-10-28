# Progress checkpoint

**Verified progress: 100 / 100 POCs built, tested and documented (71 `full`, 29 `local-variant`)** (branch `claude/relaxed-allen-usvr0o`; nothing merged, nothing public). Catalogue `built` flags (evidence column) are authoritative; see `docs/STATUS.md`.

## Approved scope adjustments
15, 19, 55 and 59 were approved as local, educational variants (titles start with "Local variant:"); 59 was corrected first (point-in-time segmentation, regression-tested). See `docs/READINESS.md` section C.

## Built as honestly labelled local variants (catalogue titles say so)
- Real services run locally: 039 PostgreSQL 16 logical decoding (no Debezium/Kafka), 052 PyIceberg + Terraform on moto, 053/058/063 moto, 041 Airflow 3 (own venv), 050/033/040 dbt, 057 local Spark (EMR price modelled, Terraform plan only), 062/064/051 Terraform plan or apply on moto.
- Simulations / models: 021 serving scheduler, 056 autoscaling (validated against Erlang C), 037 exactly-once windows (simulated broker), 068 canary rollout, 054 Step Functions-style interpreter, 060 Lake Formation semantics.
- No LLM/GPU: 014 quantisation study, 016 distillation (scikit-learn), 020 speculative decoding (toy LMs), 094 sentiment (SLM arm not run), 092 two-tower (NumPy + FAISS), 010 PDF layout (no vision model).
- Reference compositions of earlier POCs: 097 (035/047/065/071/077), 098 (001/002/006/034/047/078/084/088), 099 (017/066), plus 100 (portfolio site + repo consistency check).

## CI
- Fast `ci.yml` (py3.12 + py3.13): lint (`ruff check` and `ruff format --check`), `run_tests.py --fast`, secret scan, MCP interop test, light demo smoke, catalogue/status freshness. Heavy `heavy.yml` (path-filtered) plus an `airflow` job. Report with failures found and fixed: `docs/CI_REPORT.md`.

## Final deliverables (done)
README, `docs/FLAGSHIPS.md`, `docs/STATUS.md`, `docs/ARCHITECTURE.md`, `docs/DEMO.md`, `docs/CI_REPORT.md`, `docs/READINESS.md`.

## For the repository owner (not done, needs approval)
Make public / publish profile README or site, merge to `main` / open a PR, choose a licence, any cloud deployment, history rewrite to purge 10 accidentally committed wheels and 2 dbt logs (removed from the tree).

## Lessons recorded in CLAUDE.md
ruff format in CI; platform-sensitive trained toy models; own-env POCs; shell/`pkill` and `sleep` gotchas; GitHub MCP output size; PostgreSQL logical decoding flush and encoding gotchas.
