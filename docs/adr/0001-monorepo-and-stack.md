# ADR-0001: Monorepo layout and core technology stack

- **Status:** Accepted
- **Date:** 2026-10-13
- **Phase:** F0

## Context
Mizan will grow into several deployables (API, ingestion worker, dashboard, MCP server, model
serving) that share configuration, logging, tracing and domain types. One developer owns the
whole system for the first 24 weeks, so cross-cutting changes must be cheap, and every service
must be observable the same way from day one.

## Options considered
| Option | Pros | Cons |
|---|---|---|
| Polyrepo (one repo per service) | Independent release cycles | Shared-code changes need N PRs and version bumps; drift between services |
| Single Python package | Simplest | No boundaries; the worker would ship the API's dependencies and vice versa |
| **uv workspace monorepo** (`packages/*` libraries, `apps/*` deployables) | One lockfile, atomic cross-cutting changes, enforced boundaries via per-package dependencies | Needs discipline about what goes into shared packages |

Tooling: `uv` (fast, lockfile, workspaces) over Poetry; `ruff` replaces flake8, isort, black and
bandit; `mypy --strict` because agent-to-agent contracts are typed (Pydantic) and type errors
there are silent quality bugs.

Web framework: FastAPI (async, Pydantic-native, OpenAPI) over Django (too much we would not use)
and Litestar (smaller ecosystem).

## Decision
A uv workspace monorepo. Shared code lives in `packages/` (starting with `mizan-core`:
settings, structured logging, OpenTelemetry tracing, health primitives). Deployables live in
`apps/` and depend only on what they need. Python 3.12, FastAPI, Pydantic v2, structlog,
OpenTelemetry from the first commit.

## Consequences
- Every service gets settings, JSON logs with `trace_id`, and readiness checks for free.
- One `uv.lock` means one dependency graph; a library upgrade is tested against every app at once.
- Docker images install only their package's dependency closure (`uv sync --package`).
- Risk: `mizan-core` becoming a dumping ground. Rule: code moves to `core` only when a second
  app needs it.
- **How we will know it was wrong:** CI time for unrelated changes exceeds ~10 min, or two apps
  need conflicting versions of the same dependency.
