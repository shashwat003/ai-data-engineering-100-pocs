PY ?= python3
export PYTHONPATH := .:../../libs:../065-mlflow-registry-promotion
export MLFLOW_DISABLE_AGENT_HINT := 1

.PHONY: setup demo test lint clean
setup:
	$(PY) -m pip install -r ../../requirements-dev.txt
demo:
	$(PY) -m model_card_generator.main
test:
	$(PY) -m pytest -q
lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check .
clean:
	rm -rf .pytest_cache results/*.json results/*.md
