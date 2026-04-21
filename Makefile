VENV_DIR := .venv
PYTHON := $(VENV_DIR)/bin/python
PIP := $(VENV_DIR)/bin/pip
VENV_READY := $(VENV_DIR)/.deps-installed

.PHONY: venv test test-db

$(PYTHON):
	python3 -m venv $(VENV_DIR)

$(VENV_READY): $(PYTHON)
	$(PIP) install --upgrade pip
	$(PIP) install ".[dev]"
	touch $(VENV_READY)

venv: $(VENV_READY)

test: $(VENV_READY)
	$(PYTHON) -m pytest -q

test-db: $(VENV_READY)
	ENABLE_DB_TESTS=1 $(PYTHON) -m pytest -q
