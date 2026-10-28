PY ?= python3

.PHONY: setup lint test check demo catalogue results secrets
setup:
	$(PY) -m pip install -r requirements-dev.txt
lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check .
test:
	$(PY) scripts/run_tests.py
secrets:
	$(PY) scripts/check_secrets.py
check: lint test secrets          # what CI runs
demo:                             # regenerate every POC's results/ and embed them in its README
	@for d in pocs/*/; do $(MAKE) -C $$d demo >/dev/null || exit 1; done
	$(PY) scripts/embed_results.py
catalogue:
	$(PY) scripts/build_catalogue.py
