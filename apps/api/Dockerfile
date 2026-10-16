# syntax=docker/dockerfile:1.7
# Build from the repository root:  docker build -f apps/api/Dockerfile .

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# 1) Third-party dependencies only: this layer is cached until uv.lock changes.
COPY pyproject.toml uv.lock ./
# Every workspace member's manifest must exist for uv to resolve the workspace.
COPY packages/mizan-core/pyproject.toml packages/mizan-core/
COPY packages/mizan-ingestion/pyproject.toml packages/mizan-ingestion/
COPY packages/mizan-retrieval/pyproject.toml packages/mizan-retrieval/
COPY apps/api/pyproject.toml apps/api/
COPY apps/worker/pyproject.toml apps/worker/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package mizan-api

# 2) Our own code.
COPY packages/mizan-core packages/mizan-core
COPY packages/mizan-ingestion packages/mizan-ingestion
COPY packages/mizan-retrieval packages/mizan-retrieval
COPY apps/api apps/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package mizan-api


FROM python:3.12-slim AS runtime

RUN useradd --create-home --uid 10001 mizan \
    && mkdir -p /app/data/hf /app/data/embeddings \
    && chown -R mizan:mizan /app
WORKDIR /app
COPY --from=builder --chown=mizan:mizan /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MIZAN_LOG__JSON_FORMAT=true \
    HF_HOME=/app/data/hf \
    MIZAN_RETRIEVAL__EMBEDDINGS_CACHE=/app/data/embeddings/cache.sqlite

USER mizan
EXPOSE 8000

# Liveness only: readiness (/health/ready) waits for the embedding model to load.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]

CMD ["uvicorn", "mizan_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
