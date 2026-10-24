# Mizan

Agentic, measurable legal research platform for Azerbaijani law.

Mizan answers legal questions with citations to the exact article of the law in force at a
given date, verifies every claim against its source, and measures its own quality
continuously. See [docs/ROADMAP.md](docs/ROADMAP.md) for the full 24-week plan.

> **Status:** F4 in progress. The citation graph is live: +17% Recall@5 on multi-hop questions
> (pre-registered test), no change elsewhere. Next: Constitutional Court decisions and
> contradiction detection. F3's judge calibration awaits human labels.

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
  mizan-retrieval/     embeddings, BM25 analyser, Qdrant index, search, answering, retrieval eval
  mizan-evals/         generation eval: LLM judge, blind human labelling, Cohen's kappa
  mizan-graph/         citation extraction, temporal knowledge graph (Neo4j), graph-aware search
docs/
  adr/                 architecture decision records
  ROADMAP.md
evals/                 datasets (v0: 70, v1: 200 questions), corpus snapshot, baselines, results
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

## Evaluation (F3)

Two layers ([ADR-0005](docs/adr/0005-evaluation-methodology.md)):

- **Retrieval gate on every push.** CI rebuilds the index from a frozen, deterministic corpus
  snapshot (no database, no scraping), evaluates the serving configuration and compares it with
  a committed baseline by paired bootstrap. A change fails only when a metric's whole 95%
  interval is below zero, or Recall@5 falls under 0.75.
- **Generation eval before releases.** Retrieve, answer, then judge each answer against the
  gold provisions in force on the question's date. Mechanical checks (verbatim quotes, citing a
  gold article) are computed in code; only correctness is judged, by a different model from the
  answerer, and the judge is calibrated against blind human labels (Cohen's kappa >= 0.6).

```bash
uv run mizan-graph gate                           # what CI runs (dense + graph, F4)
uv run mizan-eval run --sample 60                 # paced for Groq's free tier, resumable
uv run mizan-eval report
uv run mizan-eval label --labeller <name>         # blind: the judge's verdict is never shown
uv run mizan-eval kappa                           # judge vs human agreement
```

## Knowledge graph (F4)

Articles cite each other ("bu Məcəllənin 70-ci maddəsinin “b” bəndi"). A grammar extracts the
citations (633 in the current text, none unresolved), and each becomes a temporal edge in
Neo4j, valid while the citing text is in force ([ADR-0006](docs/adr/0006-knowledge-graph.md)).
Search keeps the dense ranking and gives the 5th place to the most similar article the best hit
cites. If Neo4j is down, search falls back to dense results.

Constitutional Court decisions are part of the law in force. They are ingested with the acts
the Code links to, linked to the articles they rule on, and added to the context of `/v1/ask`.
The consolidated text can lag behind them: Art. 144(3) was suspended on 2026-02-18 and still
reads the same. Conflicts between norms are resolved by the collision rules of the
Constitutional Law "Normativ hüquqi aktlar haqqında" (`mizan_graph.conflicts`).

```bash
uv run mizan-ingest linked                        # register linked acts, store court decisions
uv run mizan-ingest diff 46943 art:69 2020-01-01 2026-10-01   # also GET /v1/diff
uv run mizan-graph build                          # extract citations, rewrite the graph
uv run mizan-graph neighbours art:70              # who cites Art. 70, what it cites
uv run mizan-graph eval                           # dense vs graph policies on v1 + multi-hop sets
uv run mizan-graph gate                           # what CI runs: the serving path vs baseline
```

## Quality gates

| Command | What it checks |
|---|---|
| `uv run ruff check .` | lint, import order, security (bandit rules), async pitfalls |
| `uv run ruff format --check .` | formatting |
| `uv run mypy packages apps` | strict typing |
| `uv run pytest --cov` | unit tests + coverage |
| `MIZAN_INTEGRATION=1 uv run pytest -m integration` | real Postgres: migrations + `alembic check`, ingestion pipeline end to end, dependency checks |
| `uv run mizan-graph gate` | quality of the serving path (dense + graph) vs the committed baseline (paired bootstrap) |

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
| [0005](docs/adr/0005-evaluation-methodology.md) | Versioned datasets, mechanical metrics first, calibrated judge, CI retrieval gate |
| [0006](docs/adr/0006-knowledge-graph.md) | Grammar-extracted temporal citation graph in Neo4j (derived, rebuildable); one reserved top-5 slot, pre-registered test |

## Disclaimer

Mizan is a research tool and does not provide legal advice.
