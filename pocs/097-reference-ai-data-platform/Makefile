PY ?= python3
export PYTHONPATH := .:../../libs:../035-medallion-lakehouse-duckdb:../047-idempotent-vector-ingestion:../065-mlflow-registry-promotion:../071-llm-tracing-otel:../077-data-contracts-ci-gate

.PHONY: setup demo test lint clean
setup:
	$(PY) -m pip install -r ../../requirements-dev.txt
demo:
	$(PY) -m reference_ai_data_platform.main
test:
	$(PY) -m pytest -q
lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check .
clean:
	rm -rf .pytest_cache results/*.json results/*.md
