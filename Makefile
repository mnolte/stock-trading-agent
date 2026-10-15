.PHONY: install run lint test run-backend run-frontend smoke-data smoke-decision

PYTHON := .venv/bin/python
PIP := .venv/bin/pip
UVICORN := .venv/bin/uvicorn
ALEMBIC := .venv/bin/alembic
RUFF := .venv/bin/ruff
MYPY := .venv/bin/mypy
PYTEST := .venv/bin/pytest
PNPM := $(shell command -v pnpm 2>/dev/null || echo $(HOME)/.local/bin/pnpm)

install:
	python3 -m venv .venv
	$(PIP) install -U pip
	$(PIP) install -e ".[dev]"
	cd frontend && PATH="$(HOME)/.local/bin:$(PATH)" $(PNPM) install --allow-build=esbuild

run-backend:
	$(UVICORN) app.api.main:app --host 127.0.0.1 --port 8001 --reload

run-frontend:
	cd frontend && PATH="$(HOME)/.local/bin:$(PATH)" $(PNPM) dev

run:
	@trap 'kill $$(jobs -p) 2>/dev/null' EXIT; \
	$(UVICORN) app.api.main:app --host 127.0.0.1 --port 8001 --reload & \
	cd frontend && PATH="$(HOME)/.local/bin:$(PATH)" $(PNPM) dev & \
	wait

lint:
	cd backend && ../$(RUFF) check .
	cd backend && ../$(MYPY) .
	cd frontend && PATH="$(HOME)/.local/bin:$(PATH)" $(PNPM) lint
	cd frontend && PATH="$(HOME)/.local/bin:$(PATH)" $(PNPM) typecheck

test:
	cd backend && ../$(PYTEST) -v
	cd frontend && PATH="$(HOME)/.local/bin:$(PATH)" $(PNPM) test

smoke-data:
	cd backend && ../$(PYTHON) -m app.smoke_data

smoke-decision:
	cd backend && ../$(PYTHON) -m app.smoke_decision $(SYMBOL)
