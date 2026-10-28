PY ?= python3
export PYTHONPATH := .:../../libs:../017-onnx-edge-inference-service:../066-drift-monitoring

.PHONY: setup demo test lint clean
setup:
	$(PY) -m pip install -r ../../requirements-dev.txt
demo:
	$(PY) -m reference_edge_cloud_slm.main
test:
	$(PY) -m pytest -q
lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check .
clean:
	rm -rf .pytest_cache results/*.json results/*.md
