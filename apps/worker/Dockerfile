# syntax=docker/dockerfile:1.7
# Build from the repository root:  docker build -f apps/worker/Dockerfile .
# The same image runs the worker and database migrations (`alembic upgrade head`).

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
# Every workspace member's manifest must exist for uv to resolve the workspace.
COPY packages/mizan-agents/pyproject.toml packages/mizan-agents/
COPY packages/mizan-core/pyproject.toml packages/mizan-core/
COPY packages/mizan-ingestion/pyproject.toml packages/mizan-ingestion/
COPY packages/mizan-retrieval/pyproject.toml packages/mizan-retrieval/
COPY packages/mizan-evals/pyproject.toml packages/mizan-evals/
COPY packages/mizan-graph/pyproject.toml packages/mizan-graph/
COPY apps/worker/pyproject.toml apps/worker/
COPY apps/api/pyproject.toml apps/api/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package mizan-worker

COPY packages/mizan-core packages/mizan-core
COPY packages/mizan-ingestion packages/mizan-ingestion
COPY apps/worker apps/worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package mizan-worker


FROM python:3.12-slim AS runtime

RUN useradd --create-home --uid 10001 mizan \
    && mkdir -p /app/data/raw && chown -R mizan:mizan /app
WORKDIR /app
COPY --from=builder --chown=mizan:mizan /app/.venv /app/.venv
COPY --chown=mizan:mizan alembic.ini ./
COPY --chown=mizan:mizan packages/mizan-ingestion/migrations packages/mizan-ingestion/migrations

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MIZAN_LOG__JSON_FORMAT=true

USER mizan

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["arq", "--check", "mizan_worker.main.WorkerSettings"]

CMD ["arq", "mizan_worker.main.WorkerSettings"]
