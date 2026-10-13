# Mizan

Agentic, measurable legal research platform for Azerbaijani law.

Mizan answers legal questions with citations to the exact article of the law in force at a
given date, verifies every claim against its source, and measures its own quality
continuously. See [docs/ROADMAP.md](docs/ROADMAP.md) for the full 24-week plan.

> **Status:** Phase F0 (foundation). No legal functionality yet.

## Architecture (target)

```
 client ──▶ API gateway (FastAPI) ──▶ agent orchestrator ──▶ retrieval (Qdrant + BM25 + rerank)
                │                          │                      knowledge graph (Neo4j)
                │                          └──▶ LLM gateway (routing, caching, budgets)
                └── every step traced (OpenTelemetry) ──▶ observability + online eval
```

## Repository layout

```
apps/
  api/                 FastAPI gateway (deployable)
packages/
  mizan-core/          settings, structured logging, tracing, health primitives
docs/
  adr/                 architecture decision records
  ROADMAP.md
evals/                 golden datasets and eval runners (from F3)
infra/                 deployment manifests (from F9)
compose.yaml           local stack: Postgres, Redis, Qdrant, Neo4j, API
```

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/), Docker.

```bash
cp .env.example .env
docker compose up -d --build        # full stack, API on http://127.0.0.1:18000
curl http://127.0.0.1:18000/health/ready
```

Develop on the host against the dockerised services:

```bash
uv sync
docker compose up -d postgres redis qdrant neo4j
uv run uvicorn mizan_api.main:create_app --factory --reload --port 8080
```

Host ports default to the 1xxxx range (Postgres `15432`, Redis `16379`, Qdrant `16333`,
Neo4j `17687`, API `18000`) so the stack can run next to other local projects. Override them with
`MIZAN_HOST_PORT_*` in `.env`.

## Quality gates

| Command | What it checks |
|---|---|
| `uv run ruff check .` | lint, import order, security (bandit rules), async pitfalls |
| `uv run ruff format --check .` | formatting |
| `uv run mypy packages apps` | strict typing |
| `uv run pytest --cov` | unit tests + coverage |
| `MIZAN_INTEGRATION=1 uv run pytest -m integration` | real dependency checks against the compose stack |

All of the above run in CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)), followed by a
Docker image build. Install the git hooks once with `uv run pre-commit install`.

## Operational conventions

- **Configuration:** environment variables only, prefix `MIZAN_`, nested with `__`
  (`MIZAN_POSTGRES__HOST`). Development secrets are rejected at startup in staging/production.
- **Health:** `/health/live` never touches dependencies; `/health/ready` checks every backing
  service concurrently with a per-check timeout and returns `503` if any is down. Error details
  go to logs, not to the response.
- **Tracing:** every response carries `X-Trace-Id`; incoming W3C `traceparent` headers are
  continued. Every log line carries `trace_id` / `span_id`. Set
  `MIZAN_TELEMETRY__OTLP_ENDPOINT` to export spans to Jaeger / Tempo.
- **Logs:** JSON in containers, human-readable on the host. Liveness-probe access logs are dropped.

## Decisions

| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-monorepo-and-stack.md) | uv workspace monorepo; FastAPI, Pydantic, structlog, OpenTelemetry |
| [0002](docs/adr/0002-vector-database.md) | Qdrant for retrieval, Postgres as source of truth |

## Disclaimer

Mizan is a research tool and does not provide legal advice.
