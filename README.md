# Mizan

Agentic, measurable legal research platform for Azerbaijani law.

Mizan answers legal questions with citations to the exact article of the law in force at a
given date, verifies every claim against its source, and measures its own quality
continuously. See [docs/ROADMAP.md](docs/ROADMAP.md) for the full 24-week plan.

> **Status:** F2 done: temporal retrieval over the Labour Code's full history (Recall@5 0.93
> on a 70-question eval) and cited answers via `/v1/ask`. Next: F3, the evaluation foundation.

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
  worker/              Arq worker: scheduled ingestion (deployable; also runs migrations)
packages/
  mizan-core/          settings, structured logging, tracing, health primitives
  mizan-ingestion/     e-qanun client, Word-HTML parser, temporal storage, chunking
    migrations/        Alembic migrations
  mizan-retrieval/     embeddings, BM25 analyser, Qdrant index, search, answering, eval
docs/
  adr/                 architecture decision records
  ROADMAP.md
evals/                 datasets (v0: 70 retrieval questions) and results per phase
infra/                 deployment manifests (from F9)
compose.yaml           local stack: Postgres, Redis, Qdrant, Neo4j, migrate, worker, API
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

## Legal data (F1)

Source: [e-qanun.az](https://e-qanun.az), the official legal database of the Ministry of
Justice. Every consolidated version of a document is stored with its validity interval, so
any question can be answered *as of* a date ([ADR-0003](docs/adr/0003-temporal-versioning.md)).

```bash
uv run alembic upgrade head                                  # schema
uv run mizan-ingest sync 46943                               # Labour Code, all versions
uv run mizan-ingest as-of 46943 2021-06-01 --key art:69      # Article 69 as it stood then
uv run mizan-ingest inspect path/to/file.html                # parser statistics for one file
```

What the parser handles, all found in the real data:

- mixed encodings: files declare windows-1251 but some are windows-1254; detected per file;
- structure from layout: sections, chapters (Azerbaijani ordinals: "Qırx səkkizinci fəsil"),
  articles (`Maddə 7-1.`), parts, points, items, annexes with model forms;
- 25 years of format drift: `<h3>` vs bold headings, small-caps casing artefacts, endnote
  wording variants;
- amendment history: 625 endnotes, each linked to the amending act and its date.

Being polite to a public-service site is built in: an honest User-Agent, one request at a time,
at most one every 1.5 s, and historical versions are downloaded once and then served from disk.
The worker re-syncs tracked documents nightly; a re-run downloads only the current text and
writes nothing if it has not changed.

## Retrieval and answering (F2)

```bash
uv run mizan-retrieval index                         # embed + index all versions (resumable)
uv run mizan-retrieval search "Məni işdən qova bilərlər?" --as-of 2020-01-01
uv run mizan-retrieval eval                          # 17 variants, report in evals/results/
curl "http://127.0.0.1:18000/v1/search?q=sınaq+müddəti&as_of=2010-01-01"
curl -X POST http://127.0.0.1:18000/v1/ask -H "Content-Type: application/json" \
     -d '{"question": "İşdən çıxmaq üçün neçə gün əvvəl xəbər verməliyəm?"}'
```

- **Configuration chosen by measurement** ([ADR-0004](docs/adr/0004-retrieval-configuration.md)):
  dense bge-m3 over article chunks, filtered to the text in force on the requested date.
  Recall@5 **0.929** [0.87, 0.98], MRR 0.831, 376 ms p50 on a laptop CPU. Hybrid fusion,
  cross-encoder reranking and LLM query rewriting were all built and measured; none paid for
  itself on this question set, and each can be switched on by configuration.
- **Answers cite the statute verbatim.** `/v1/ask` uses Groq (`openai/gpt-oss-120b`) by
  default, or Claude with its native Citations API. With Groq, every quote the model returns
  is checked to occur in the source it names; unverifiable quotes are dropped and counted.
- Long articles are retrieved by part and answered from the whole article (parent-child).
- Set `MIZAN_LLM__GROQ_API_KEY` to enable `/v1/ask`; `/v1/search` needs no key.

## Quality gates

| Command | What it checks |
|---|---|
| `uv run ruff check .` | lint, import order, security (bandit rules), async pitfalls |
| `uv run ruff format --check .` | formatting |
| `uv run mypy packages apps` | strict typing |
| `uv run pytest --cov` | unit tests + coverage |
| `MIZAN_INTEGRATION=1 uv run pytest -m integration` | real Postgres: migrations + `alembic check`, ingestion pipeline end to end, dependency checks |

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
| [0003](docs/adr/0003-temporal-versioning.md) | Versioned snapshots with derived validity intervals; content-addressed text |
| [0004](docs/adr/0004-retrieval-configuration.md) | Dense bge-m3, article chunks; hybrid, reranking and rewriting measured and left off |

## Disclaimer

Mizan is a research tool and does not provide legal advice.
