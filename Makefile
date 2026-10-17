.PHONY: install run lint test run-backend run-frontend smoke-data smoke-decision

PYTHON := .venv/bin/python
PIP := .venv/bin/pip
UVICORN := .venv/bin/uvicorn
ALEMBIC := .venv/bin/alembic
RUFF := .venv/bin/ruff
MYPY := .venv/bin/mypy
PYTEST := .venv/bin/pytest
PNPM := $(shell command -v pnpm 2>/dev/null || echo $(HOME)/.local/bin/pnpm)

# Backend port: SA_PORT from .env (fallback 8001). The frontend proxy gets
# the same value via VITE_API_PORT so both sides always agree.
SA_PORT ?= $(shell sed -n 's/^SA_PORT=\([0-9][0-9]*\).*/\1/p' .env)
SA_PORT := $(if $(strip $(SA_PORT)),$(strip $(SA_PORT)),8001)

install:
	python3 -m venv .venv
	$(PIP) install -U pip
	$(PIP) install -e ".[dev]"
	cd frontend && PATH="$(HOME)/.local/bin:$(PATH)" $(PNPM) install --allow-build=esbuild

run-backend:
	$(UVICORN) app.api.main:app --host 127.0.0.1 --port $(SA_PORT) --reload

run-frontend:
	cd frontend && VITE_API_PORT=$(SA_PORT) PATH="$(HOME)/.local/bin:$(PATH)" $(PNPM) dev

run:
	@trap 'kill $$(jobs -p) 2>/dev/null' EXIT; \
	$(UVICORN) app.api.main:app --host 127.0.0.1 --port $(SA_PORT) --reload & \
	cd frontend && VITE_API_PORT=$(SA_PORT) PATH="$(HOME)/.local/bin:$(PATH)" $(PNPM) dev & \
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

smoke-execution:
	cd backend && ../$(PYTHON) -m app.smoke_execution
