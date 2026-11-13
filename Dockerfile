# syntax=docker/dockerfile:1
# Stock Trading Agent — production image (docs/SPEC.md, VPS deployment).
# The runtime stage is non-root and serves the built frontend from the
# backend; the entrypoint is `python -m app.serve` — the only source of
# the bind address is SA_HOST/SA_PORT (never bind flags anywhere).

######################## frontend build ########################
FROM node:22-alpine@sha256:1314fb30ec37592d5845e5a160d522c0e680972aa534549940b8d73bde88481a AS frontend
WORKDIR /fe
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile --allow-build=esbuild
COPY frontend/ ./
RUN pnpm build

######################## python deps ########################
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS deps
WORKDIR /srv
RUN pip install --no-cache-dir "uv==0.13.7"
COPY pyproject.toml uv.lock ./
# --no-emit-project: the app runs from the source tree at /srv/backend so
# app.config.ROOT keeps pointing at the layout it expects (.env, prompts,
# alembic.ini, frontend/dist). Installing the wheel would break that.
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt \
        -o /tmp/requirements.txt \
    && uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python --no-cache \
        -r /tmp/requirements.txt

######################## test image (CI) ########################
FROM deps AS test
RUN uv export --frozen --no-emit-project --extra dev --format requirements-txt \
        -o /tmp/requirements-dev.txt \
    && uv pip install --python /opt/venv/bin/python --no-cache \
        -r /tmp/requirements-dev.txt
COPY backend/ /srv/backend/
# scripts/coverage_summary.py is exercised by backend/tests/.
COPY scripts/ /srv/scripts/
COPY --from=frontend /fe/dist /srv/frontend/dist
ENV PYTHONPATH=/srv/backend \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv
# Repo-boundary tests (they read files that don't ship in the image:
# .git, Dockerfile, Makefile, deploy/, compose files) run in the host CI
# job — the image runs the app test suite.
CMD ["/opt/venv/bin/python", "-m", "pytest", "backend/tests", \
     "-m", "not slow and not perf", \
     "-n", "auto", "--dist", "worksteal", "--durations=20", \
     "--deselect", "backend/tests/test_security.py::test_live_alpaca_url_not_present", \
     "--deselect", "backend/tests/test_compose.py", \
     "--deselect", "backend/tests/test_deploy_scripts.py", \
     "--deselect", "backend/tests/test_serve.py::test_no_host_port_flags_anywhere", \
     "--deselect", "backend/tests/test_serve.py::test_entrypoint_is_app_serve", \
     "--deselect", "backend/tests/test_scan_security.py::test_run_audits_pins_python_312", \
     "--deselect", "backend/tests/test_no_silent_failures.py", \
     "--deselect", "backend/tests/test_readme.py", \
     "--deselect", "backend/tests/test_shutdown.py::test_lock_recycled_pid_is_stale"]

######################## runtime ########################
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Amsterdam \
    PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    SA_DATA_DIR=/data \
    SA_DB_PATH=/data/stock-agent.db \
    SA_HOST=0.0.0.0 \
    SA_PORT=8000
# restic: offsite backups; tzdata: Europe/Amsterdam scheduling.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates restic tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 stockagent \
    && useradd --uid 10001 --gid stockagent --home-dir /data \
        --shell /usr/sbin/nologin stockagent \
    && mkdir -p /data && chown stockagent:stockagent /data && chmod 0700 /data
WORKDIR /srv
COPY --from=deps /opt/venv /opt/venv
COPY pyproject.toml /srv/pyproject.toml
COPY backend/ /srv/backend/
COPY --from=frontend /fe/dist /srv/frontend/dist
# Item 084: the runtime image ships no tests or eval data (the test
# stage above keeps them), and /srv belongs to root — world-readable,
# not writable by the app user. Everything writable lives on /data.
RUN rm -rf /srv/backend/tests /srv/backend/eval \
    && chown -R root:root /srv && chmod -R a+rX /srv
USER stockagent
WORKDIR /srv/backend
ENV PYTHONPATH=/srv/backend
EXPOSE 8000
ENTRYPOINT ["python", "-m", "app.serve"]
