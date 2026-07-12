PYTHON ?= python3
VENV ?= python/.venv

.PHONY: bootstrap test test-python test-rust format-check clean

test: test-python test-rust

bootstrap: $(VENV)/bin/python

$(VENV)/bin/python: python/pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -e './python[test]'

test-python: bootstrap
	$(VENV)/bin/ruff check python
	$(VENV)/bin/mypy --config-file python/pyproject.toml python/src
	$(VENV)/bin/python -m unittest discover -s python/tests -v

test-rust:
	cargo test --manifest-path rust/Cargo.toml

format-check:
	python -m json.tool openapi/forge-accelerator-v1.openapi.json >/dev/null
	python -m json.tool schema/forge-accelerator-v1.schema.json >/dev/null
	cargo fmt --manifest-path rust/Cargo.toml -- --check

clean:
	rm -rf $(VENV) python/dist rust/target
