VENV_DIR := .venv
PYTHON := $(VENV_DIR)/bin/python
PIP := $(VENV_DIR)/bin/pip
VENV_READY := $(VENV_DIR)/.deps-installed

.PHONY: venv test db-up db-down db-health

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

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

db-health:
	docker compose exec -T postgres pg_isready -U $${POSTGRES_USER:-gefion} -d $${POSTGRES_DB:-gefion}
