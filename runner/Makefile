PYTHON ?= python3
VENV ?= .venv

.PHONY: bootstrap test clean

bootstrap: $(VENV)/bin/pytest

$(VENV)/bin/pytest: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -e '.[test]'

test: bootstrap
	$(VENV)/bin/pytest
	$(VENV)/bin/ruff check .
	MYPYPATH=src $(VENV)/bin/mypy --strict src/forge_runner tests
	@if command -v shellcheck >/dev/null; then shellcheck deploy/install.sh; else echo "shellcheck unavailable; skipped"; fi

clean:
	rm -rf $(VENV) dist .pytest_cache .ruff_cache .mypy_cache
