# Deploying Mizan

Mizan runs as one Docker Compose stack: the API, the worker, Postgres, Redis, Qdrant and
Neo4j, plus Temporal and the observability stack if wanted. Nothing in it needs a GPU.

## What it needs
| | minimum | why |
|---|---|---|
| RAM | 8 GB (16 GB comfortable) | bge-m3 in the API (~2.3 GB with torch), Neo4j (~1–2 GB), Qdrant, Postgres |
| CPU | 4 cores (8 for ~25 concurrent users) | query embedding is the bottleneck: 88 ms per query per core-set ([F11 load](../evals/results/f11_load/README.md)) |
| disk | 20 GB | images, model cache, databases, raw e-qanun snapshots |
| outbound | Groq (and OpenRouter as fallback), Hugging Face (first model download), e-qanun.az (ingestion) | |

## Options (check prices yourself: they change)
| option | fits | notes |
|---|---|---|
| a VPS with 8 vCPU / 16 GB (Hetzner, OVH, Contabo, DigitalOcean …) | ✅ | simplest: the same `compose.yaml`, a reverse proxy for TLS |
| Oracle Cloud "Always Free" ARM (4 cores / 24 GB) | ✅ with care | free, but arm64: build the images for `linux/arm64`; capacity is not always available |
| PaaS (Render, Railway, Fly.io) | ⚠️ | per-service memory limits make bge-m3 + Neo4j expensive; managed Postgres/Redis help |
| Kubernetes | overkill | for one process; revisit with several API replicas and a shared embedding service |

## Checklist
1. **Secrets** in a `.env` that is not in git:
   - `MIZAN_ENVIRONMENT=production`: development secrets and disabled auth are refused at
     startup;
   - `MIZAN_AUTH__ENABLED=true` and `MIZAN_AUTH__JWT_SECRET`, 32+ random characters;
   - real `MIZAN_POSTGRES__PASSWORD` and the Neo4j password;
   - `MIZAN_LLM__GROQ_API_KEY`, and optionally `MIZAN_LLM__OPENROUTER_API_KEY` for failover.
2. **TLS** in front of the API, with Caddy or nginx and Let's Encrypt.
   - Bind every service except the proxy to 127.0.0.1. The compose file already binds the
     databases there.
3. **Database roles (OWASP LLM02 next step).** The API should log in as a role that is a
   member of `mizan_app`, not as the owner. Today it logs in as the owner and switches with
   `SET LOCAL ROLE`. This needs:
   - a `LOGIN` role for the API, with `SELECT` on the corpus tables and membership in
     `mizan_app`;
   - the migrations keep the owner;
   - two sets of credentials in compose.
4. **First data load.** `docker compose up -d` runs the migrations. The worker then ingests
   e-qanun and builds the index and graph, as in the README quick start.
5. **Tokens** for users: `uv run mizan-token --subject <id> --tenant <tenant> --scopes ask`.
6. **Monitoring.**
   - Start `compose --profile observability` (OTel Collector → ClickHouse), or point
     `MIZAN_TELEMETRY__OTLP_ENDPOINT` at a hosted backend.
   - Alerts come from `mizan-eval drift` (online faithfulness) and from `/health/ready`.
7. **Backups:** nightly `pg_dump`.
   - Qdrant and Neo4j are derived and can be rebuilt from Postgres. Back them up only to save
     rebuild time.
8. **Budgets:** `MIZAN_LLM__DAILY_TOKENS_PER_TENANT` and the per-minute rate limit bound
   spend. Set them before sharing a token.
