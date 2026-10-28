# syntax=docker/dockerfile:1
# Stock Trading Agent — production image (docs/SPEC.md, VPS deployment).
# The runtime stage is non-root and serves the built frontend from the
# backend; the entrypoint is `python -m app.serve` — the only source of
# the bind address is SA_HOST/SA_PORT (never bind flags anywhere).

######################## frontend build ########################
FROM node:22-alpine AS frontend
WORKDIR /fe
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --allow-build=esbuild
COPY frontend/ ./
RUN pnpm build

######################## python deps ########################
FROM python:3.12-slim AS deps
WORKDIR /srv
RUN pip install --no-cache-dir "uv>=0.9,<1"
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
COPY --from=frontend /fe/dist /srv/frontend/dist
ENV PYTHONPATH=/srv/backend \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv
CMD ["/opt/venv/bin/python", "-m", "pytest", "backend/tests", "-m", "not slow and not perf"]

######################## runtime ########################
FROM python:3.12-slim AS runtime
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
RUN chown -R stockagent:stockagent /srv /data
USER stockagent
WORKDIR /srv/backend
ENV PYTHONPATH=/srv/backend
EXPOSE 8000
ENTRYPOINT ["python", "-m", "app.serve"]
