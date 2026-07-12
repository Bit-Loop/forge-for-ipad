PYTHON ?= python3
VENV ?= .venv

.PHONY: bootstrap test build clean

bootstrap: $(VENV)/bin/pytest

$(VENV)/bin/pytest: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -e '.[test]'

test: bootstrap
	$(VENV)/bin/pytest

build: bootstrap
	$(VENV)/bin/pip install build
	$(VENV)/bin/python -m build

clean:
	rm -rf $(VENV) dist .pytest_cache src/*.egg-info
