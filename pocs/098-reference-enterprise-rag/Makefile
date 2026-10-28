PY ?= python3
export PYTHONPATH := .:../../libs:../001-rag-hybrid-search-eval:../002-rag-citation-guard:../006-semantic-cache-gateway:../034-agent-policy-guardrails:../047-idempotent-vector-ingestion:../078-pii-scanner-classifier:../084-tamper-evident-audit-log:../088-gdpr-erasure-pipeline

.PHONY: setup demo test lint clean
setup:
	$(PY) -m pip install -r ../../requirements-dev.txt
demo:
	$(PY) -m reference_enterprise_rag.main
test:
	$(PY) -m pytest -q
lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check .
clean:
	rm -rf .pytest_cache results/*.json results/*.md
