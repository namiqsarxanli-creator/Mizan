# ADR-0008: Observability: OpenTelemetry GenAI spans, ClickHouse, and a quality loop in Postgres

- **Status:** Accepted
- **Date:** 2026-10-25
- **Phase:** F6

## Context
After F5, one question can mean a planner call, several parallel tool pipelines, a drafter
call, a verifier call and a second research round. When an answer is wrong, slow or expensive,
we need to see which step made it so. When quality drifts in production, we need to notice
before users do. The offline evals (F3, F5) only measure fixed datasets.

## Decision

### 1. Instrumentation: OpenTelemetry with the GenAI semantic conventions
Spans use the standard names and attributes rather than a format of our own:

| Span | Example name | Key attributes |
|---|---|---|
| Agent step | `invoke_agent orchestrator`, `invoke_agent planner`, ... | `gen_ai.operation.name=invoke_agent`, `gen_ai.agent.name` |
| Tool call | `execute_tool search_law`, ... | `gen_ai.operation.name=execute_tool`, `gen_ai.tool.name` |
| LLM call | `chat openai/gpt-oss-120b` | `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens/output_tokens`, `gen_ai.response.finish_reasons` |

A request is one trace: FastAPI server span → orchestrator → steps → tool and LLM spans.
Each LLM attempt is its own span, so retries are visible.

Mizan-specific attributes live under `mizan.*`:
- `mizan.cost.usd`: an estimate from a configurable price table. Unknown models get no value
  rather than a wrong one.
- `mizan.prompt.version`: the first 12 hex characters of the prompt template's SHA-256. It
  changes exactly when the text changes, which is the basis for prompt A/B analysis.
- Verdict, confidence, iterations, and degradation flags.

Any OTel backend can read these spans: Jaeger, Tempo, Langfuse, or ClickHouse below.

### 2. Storage: OTel Collector → ClickHouse, 30-day retention
- The application exports OTLP/HTTP to a Collector, which writes `otel.otel_traces` in
  ClickHouse with a 720 h TTL.
- ClickHouse is columnar and handles the questions we ask well: "p95 of `chat` spans by
  model and prompt version", "cost per day".
- The Collector, not the application, owns storage concerns: batching, retries, memory limits,
  retention, and dropping health-probe spans. The application only speaks OTLP, so the backend
  can be swapped without code changes.

### 3. PII: redacted twice
- **In code:** `mizan_core.genai.redact` masks e-mails, phone numbers, card numbers, FIN codes
  and ID card numbers before user text becomes a span attribute or a database row.
- **In the Collector:** a `redaction` processor drops values that still match those patterns,
  so an attribute missed in code cannot reach storage.

### 4. The quality loop lives in Postgres, beside the data it describes
- `answers`: every `/v1/ask` and agent answer, with the redacted question, sources, model,
  prompt version, A/B variant, tokens and latency.
- `feedback`: 👍 / 👎 and a comment, via `POST /v1/feedback` with the `answer_id` returned in
  every answer.
- `online_evals`: **5%** of non-abstained answers, sampled deterministically by answer id, are
  judged for faithfulness against their own sources by an independent model (`qwen3.8-27b`).
  This runs after the response is sent, with its own concurrency limit; its failures are
  logged, never raised.

These rows are relational and long-lived, and F7/F8 train on them, so they belong in the
transactional store, not in trace storage with a 30-day TTL.

### 5. Alerts: drift and SLO, in code and in Grafana
- **Drift:** the last 7 days' mean faithfulness is more than 10% below the previous 28 days'.
- **SLO breach:** the last 7 days' mean is below 0.90.
- Both need at least 20 samples per window.
- The rule is implemented twice:
  - `mizan-eval drift`: exits 1 and posts to a chat webhook, for cron or CI.
  - A Grafana alert rule, provisioned from the repository.
- An integration test injects a synthetic quality drop into Postgres and asserts the alert.

### 6. Dashboards as code
Grafana (with the ClickHouse and Postgres data sources, the dashboard and the alert rule) is
provisioned from `infra/observability`. Panels:
- latency p50/p95;
- LLM cost by model;
- tokens by model and prompt version;
- agent outcomes;
- errors and degradations;
- a trace explorer (recent requests → span tree);
- 7-day faithfulness and the 👍 share;
- SLO stats: agent p95 < 8 s, availability ≥ 99.5%, faithfulness ≥ 0.90.

## Consequences
- `docker compose --profile observability up -d` starts ClickHouse, the Collector and
  Grafana. Telemetry is opt-in: without `MIZAN_TELEMETRY__OTLP_ENDPOINT`, spans are created
  (for trace ids) but not exported.
- Online evaluation costs about 5% of answer volume in judge calls; the sample rate is a
  setting.
- Not yet done: A/B *assignment* of prompt variants. Prompt versions are recorded, so
  comparisons by version work already.
- The Grafana Postgres data source uses development credentials; production gets a read-only
  role (F9).
