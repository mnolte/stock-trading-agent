.PHONY: install install-backend install-frontend dev run run-backend run-frontend lint lint-backend lint-frontend logs-reindex create-user test test-backend test-slow test-frontend test-e2e smoke-data smoke-decision smoke-execution readme-screens

PYTHON := .venv/bin/python
PIP := .venv/bin/pip
UVICORN := .venv/bin/uvicorn
ALEMBIC := .venv/bin/alembic
RUFF := .venv/bin/ruff
MYPY := .venv/bin/mypy
PYTEST := .venv/bin/pytest
PNPM := $(shell command -v pnpm 2>/dev/null || echo $(HOME)/.local/bin/pnpm)
FE := cd frontend && PATH="$(HOME)/.local/bin:$(PATH)" $(PNPM)

# Backend port: SA_PORT from .env (fallback 8001). The frontend proxy gets
# the same value via VITE_API_PORT so both sides always agree.
SA_PORT ?= $(shell sed -n 's/^SA_PORT=\([0-9][0-9]*\).*/\1/p' .env 2>/dev/null)
SA_PORT := $(if $(strip $(SA_PORT)),$(strip $(SA_PORT)),8001)

install: install-backend install-frontend

install-backend: ## uv sync when uv is available (uses uv.lock), else pip -e .[dev]
	@if command -v uv >/dev/null 2>&1; then \
	  uv sync --extra dev; \
	else \
	  python3 -m venv .venv && $(PIP) install -U pip && $(PIP) install -e ".[dev]"; \
	fi

install-frontend:
	$(FE) install --allow-build=esbuild

dev: ## backend (python -m app.serve, SA_RELOAD=1) + Vite dev server, Ctrl-C stops both
	@trap 'trap - INT TERM EXIT; kill 0' INT TERM EXIT; \
	  cd backend && SA_RELOAD=1 ../$(PYTHON) -m app.serve & \
	  cd frontend && VITE_API_PORT=$(SA_PORT) PATH="$(HOME)/.local/bin:$(PATH)" $(PNPM) dev & \
	  wait

run: ## build the UI, serve everything on 127.0.0.1:$(SA_PORT)
	$(FE) build
	cd backend && ../$(PYTHON) -m app.serve

run-backend:
	cd backend && SA_RELOAD=1 ../$(PYTHON) -m app.serve

run-frontend:
	cd frontend && VITE_API_PORT=$(SA_PORT) PATH="$(HOME)/.local/bin:$(PATH)" $(PNPM) dev

lint: lint-backend lint-frontend

lint-backend:
	cd backend && ../$(RUFF) check .
	cd backend && ../$(MYPY) .

lint-frontend:
	$(FE) lint
	$(FE) typecheck

logs-reindex: ## rebuild data/logs/logs.db from the log files (PR 20)
	cd backend && ../$(PYTHON) -m app.log_index

create-user: ## create or reset the single sign-in account (server only, PR 23)
	cd backend && ../$(PYTHON) -m app.auth.create_user

broker-guard: ## set + verify no_shorting and 1x margin on the paper account
	cd backend && ../$(PYTHON) -m app.broker_guard

replay: ## replay a prompt over stored snapshots: make replay PROMPT=decision.v2 FROM=2026-10-20 TO=2026-10-25 [MODEL=] [YES=1] [APPROX=1]
	cd backend && ../$(PYTHON) -m app.replay \
		--prompt $(PROMPT) --from $(FROM) --to $(TO) \
		$(if $(MODEL),--model $(MODEL),) $(if $(filter 1 true yes,$(YES)),--yes,) \
		$(if $(filter 1 true yes,$(APPROX)),--approx-reference,)

test: test-backend test-perf test-frontend test-e2e ## pytest + Vitest (+ Playwright where present)

test-backend:
	cd backend && ../$(PYTEST) -v -m "not slow and not perf" -n auto --dist worksteal --durations=15

test-perf: ## timing-budget tests, serial — CPU contention skews them
	cd backend && ../$(PYTEST) -v -m perf --durations=15

test-slow: ## pytest -m slow (multi-minute tests, e.g. the 3-month replay)
	cd backend && ../$(PYTEST) -v -m slow -n auto --dist worksteal --durations=15

test-frontend:
	$(FE) test

test-e2e:
	@if ls "$$HOME"/.cache/ms-playwright/chromium* >/dev/null 2>&1; then \
	  $(FE) exec playwright test --reporter=line; \
	else \
	  echo "skip playwright (needs browsers: cd frontend && pnpm exec playwright install chromium)"; \
	fi

readme-screens: ## README screenshots → docs/images/readme/ (simulated, seeded fixtures)
	mkdir -p docs/images/readme
	$(FE) exec playwright test --config=playwright.readme.config.ts --reporter=line
	@for f in docs/images/readme/*.png; do \
	  sz=$$(stat -c%s "$$f"); \
	  [ $$sz -gt 409600 ] && echo "optimising $$f ($$sz bytes)" && \
	    convert "$$f" -strip -define png:compression-level=9 "$${f%.png}.opt.png" && \
	    [ $$(stat -c%s "$${f%.png}.opt.png") -lt $$sz ] && mv "$${f%.png}.opt.png" "$$f" || rm -f "$${f%.png}.opt.png"; \
	  true; \
	done

smoke-data:
	cd backend && ../$(PYTHON) -m app.smoke_data

smoke-decision:
	cd backend && ../$(PYTHON) -m app.smoke_decision $(SYMBOL)

smoke-execution:
	cd backend && ../$(PYTHON) -m app.smoke_execution
