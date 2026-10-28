PY ?= python3
export PYTHONPATH := .:../../libs
export OPA_BIN ?= $(shell command -v opa 2>/dev/null)

.PHONY: setup demo test lint clean
setup:
	$(PY) -m pip install -r ../../requirements-dev.txt
demo:
	$(PY) -m policy_as_code_opa.main
test:
	$(PY) -m pytest -q
lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check .
clean:
	rm -rf .pytest_cache results/*.json results/*.md
