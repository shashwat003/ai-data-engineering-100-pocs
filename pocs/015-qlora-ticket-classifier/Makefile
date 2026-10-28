PY ?= python3
export PYTHONPATH := .:../../libs:../014-gguf-quantisation-study:../016-llm-to-slm-distillation

.PHONY: setup demo test lint clean
setup:
	$(PY) -m pip install -r ../../requirements-dev.txt
demo:
	$(PY) -m qlora_ticket_classifier.main
test:
	$(PY) -m pytest -q
lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check .
clean:
	rm -rf .pytest_cache results/*.json results/*.md
