# ADR-0011: Durable agent runs on Temporal, opt-in

- **Status:** Accepted
- **Date:** 2026-10-30
- **Phase:** F9

## Context
A research run (ADR-0007) takes 20–60 s and makes three to six LLM calls: plan, draft and verify,
times up to two rounds. It runs inside the API process.

- **Restarts:** if the process restarts (a deploy, a crash, an out-of-memory kill), the run is
  lost. The tokens it spent are lost with it, and the client has to start again.
- **Retries:** a client that retries after a dropped connection starts a second, paid run of
  the same question.

F9 asked for runs that continue where they stopped after a restart.

## Options considered
| Option | Pros | Cons |
|---|---|---|
| A. In process (current) | nothing to operate | a restart loses the run and its tokens; retries pay twice |
| B. Checkpoint each step to Postgres, resume by hand | no new infrastructure | we would write a workflow engine: leases, retries, timeouts, resumption, and their tests |
| C. Arq (already used for ingestion) | already deployed | retries whole jobs, not steps; no record of finished steps |
| D. Temporal workflow, one activity per step | step results are recorded, so replay skips finished steps; retries, timeouts, heartbeats and idempotent ids built in | one more service, and a worker process |

## Decision
**Option D, opt-in** (`MIZAN_DURABLE__ENABLED`).

- **One loop.** The workflow runs the same state machine as the in-process agent
  (`orchestrator.run_steps`). Only the execution of each step differs: `Steps` is a protocol,
  `Orchestrator` implements it in process, and `durable._ActivitySteps` as activities.
  - The two modes cannot drift apart.
  - A test runs one scripted case through both and asserts identical answers.
- **Activities wrap the in-process steps.** Timeouts, tracing and degradation stay in one place.
  When Temporal's retries run out, a step degrades exactly as in process.
  - The token budget crosses the boundary as "tokens left"; each activity returns what it spent,
    by role.
- **Heartbeats.** A step reports every few seconds, so a dead worker is detected in about 10 s
  instead of the 120 s step timeout.
- **Idempotency.** The workflow id is `agent-{tenant}-{Idempotency-Key}`.
  - A repeated request joins the run in progress, or gets the finished run's answer.
  - Only a failed run may start again.
- **Streaming** (`/v1/agent/ask/stream`) is unchanged. The API polls the workflow's `progress`
  query and forwards new step events.
- **Deployment.**
  - The dev server runs in compose (profile `durable`), with SQLite persistence on a volume.
  - `mizan-agent-worker` builds the same services as the API and serves the four activities.
  - Off by default: without Temporal the API runs the agent in process, as before.

## Evidence
- **Unit tests** (`packages/mizan-agents/tests/test_orchestrator.py`) run on the SDK's local dev
  server and check three things:
  - The durable run equals the in-process run.
  - A repeated request id returns the same answer with no new LLM calls.
  - A worker cancelled mid-draft is replaced: the plan is not paid for twice, and the answer
    and the token usage are the same.
- **End to end on 2026-10-30.** Real API, worker, Groq and Temporal in compose, streaming one
  question.
  - The worker process was killed (`Stop-Process -Force`) when the draft step began, and a
    new worker was started 5 s later.
  - The run finished with an answer at 50 s.
  - Workflow history: `plan` and `research` ran once (attempt 1); `draft` completed on
    attempt 2, on the new worker; `verify` ran once.
  - The new worker made no planner call.
  - The same `Idempotency-Key` sent again returned the answer in 0.44 s with no LLM call.

## Consequences
- **Easier:** restarts and deploys no longer lose runs or their tokens, and client retries do
  not pay twice.
- **Easier:** Temporal's UI (port 18233) shows every run's steps, inputs, outputs and retries.
- **Harder, accepted:** another service and another process to run. The dev server is not
  production Temporal. A real deployment uses Temporal Cloud or a cluster with Postgres
  persistence.
- **Harder, accepted:**
  - Workflow code must stay deterministic. `run_steps` is pure apart from its steps, and it is
    imported through the sandbox's pass-through.
  - Changing the loop changes replay of runs in flight; such a change needs Temporal
    versioning (`workflow.patched`).
- **Harder, accepted:** step events reach the client by polling (0.5 s), not by push.
- **Not changed:** `/v1/ask`, the single-shot answer, stays in process. It is one LLM call,
  and a retry costs the same as a resume.

**How we will know it was wrong:**
- Temporal's operational cost outweighs the runs it saves. Few restarts, and runs short
  enough that a retry is cheap, would mean option A was enough.
- Replay failures after code changes become frequent. The loop would then belong in the
  workflow alone, with the in-process mode dropped.
