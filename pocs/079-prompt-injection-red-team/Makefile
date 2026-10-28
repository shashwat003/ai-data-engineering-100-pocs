PY ?= python3
export PYTHONPATH := .:../../libs:../023-incident-triage-agent:../024-mcp-server-data-catalog

.PHONY: setup demo test lint clean
setup:
	$(PY) -m pip install -r ../../requirements-dev.txt
demo:
	$(PY) -m prompt_injection_red_team.main
test:
	$(PY) -m pytest -q
lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check .
clean:
	rm -rf .pytest_cache results/*.json results/*.md
