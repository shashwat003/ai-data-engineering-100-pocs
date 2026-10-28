PY ?= python3
export PYTHONPATH := .:../../libs:../047-idempotent-vector-ingestion

.PHONY: setup demo test lint clean
setup:
	$(PY) -m pip install -r ../../requirements-dev.txt
demo:
	$(PY) -m gdpr_erasure_pipeline.main
test:
	$(PY) -m pytest -q
lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check .
clean:
	rm -rf .pytest_cache results/*.json results/*.md
