PY ?= python3
export PYTHONPATH := .:../../libs:../077-data-contracts-ci-gate

.PHONY: setup demo test lint clean
setup:
	$(PY) -m pip install -r ../../requirements-dev.txt
demo:
	$(PY) -m medallion_lakehouse_duckdb.main
test:
	$(PY) -m pytest -q
lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check .
clean:
	rm -rf .pytest_cache results/*.json results/*.md data/generated
