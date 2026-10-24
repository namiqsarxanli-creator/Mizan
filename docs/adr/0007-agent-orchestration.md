# ADR-0007: Agent orchestration: our own typed state machine, LLMs only where judgement is needed

- **Status:** Accepted
- **Date:** 2026-10-25
- **Phase:** F5

## Context
F2–F4 answer with one retrieve-then-generate call. Harder questions need more steps: decompose
the question, research several provisions, write an answer, check every claim against its
source, and decide whether the result deserves to be shown at all.

Constraints:
- Groq's free tier: about 8k tokens per minute per model.
- Answers must be verifiable claim by claim (F5 DoD: faithfulness and citation accuracy ≥ 0.90).
- Every step must be bounded (iterations, time, tokens) and observable (F6).
- The service must keep working when a model or the graph fails.

## Decision

### 1. A small typed state machine of our own, not LangGraph
`PLAN → RESEARCH (parallel) → DRAFT → VERIFY → JUDGE → (RESEARCH again | DONE)`, about 200
lines (`mizan_agents/orchestrator.py`).

| | Own state machine | LangGraph |
|---|---|---|
| Control flow | explicit `for`/`if`, read top to bottom | graph DSL, conditional edges |
| Limits, budgets | ours, enforced in one place | partly built in, partly ours anyway |
| Persistence, human-in-the-loop | not needed yet | built in (checkpointers) |
| Testing | plain async functions with a scripted LLM | needs its runtime |
| Dependencies | none | langgraph, langchain-core, their release cadence |

The flow has five fixed steps and one loop. LangGraph's strengths (durable checkpoints, dynamic
graphs, human interrupts) are not needed for it, and its cost (a framework between us and every
step) is paid on every change. If F9 needs durable long-running runs, the steps are already
pure async functions with typed inputs and outputs, and they map onto LangGraph nodes
one-to-one.

### 2. LLMs only where judgement is needed
- The **planner, drafter and verifier** call LLMs.
- The **researchers** are deterministic tool pipelines:
  1. search (dense search + graph);
  2. follow the best article's explicit exceptions;
  3. add the Constitutional Court decisions in force;
  4. run the NHA collision rules.
- The **judge** is a fixed policy:
  - research again on a contradicted claim while iterations remain;
  - abstain if any claim is still contradicted, or if confidence is below the threshold;
  - otherwise answer.

Letting a model choose tools (ReAct) would cost a call per decision, add latency and new failure
modes, and choose nothing a Labour Code question needs chosen. The model's judgement is spent on
language and law, not on control flow.

### 3. Agents exchange Pydantic models only
`Plan`, `ResearchNote`, `Draft`, `ClaimCheck`, `AgentAnswer` (`extra="forbid"`). A malformed
LLM output is caught at the boundary. The gateway then gives the model one repair turn with the
validation error and fails if the output is still invalid.

### 4. The LLM gateway owns reliability
- **Routing:** planner and verifier use `gpt-oss-20b`, the drafter uses `gpt-oss-120b`. On
  Groq each model has its own rate limit, so routing also spreads the load.
- **Retries:** only transient errors (429, 5xx, timeouts) are retried, with capped exponential
  backoff and equal jitter; a provider's `retry-after` wins. 400 and 413 fail at once.
- **Circuit breaker:** a model is skipped for a cool-down after repeated failures.
- **Token budget:** each run has a budget (default 40k tokens). The gateway reserves an
  estimate before every call, so a non-converging loop stops before it overspends.

### 5. Verification is mechanical first
Every claim carries a verbatim quote from a named source. The quote check is code: whitespace
and case are ignored, nothing else. Then one batched verifier call labels each claim
`supported / contradicted / not_enough_info` against its own source text only. Confidence is
the share of supported claims, with full credit only for verbatim quotes.

### 6. Degrade, do not fail
| Failure | Behaviour |
|---|---|
| Planner fails | the question itself becomes the plan |
| Verifier fails | every claim is unverified, so confidence is 0 and the judge abstains |
| Graph fails | search falls back to dense; research loses exceptions and decisions |
| Drafter fails | abstain |

An unchecked answer is never presented as a checked one.

### 7. Streaming
`POST /v1/agent/ask/stream` sends one SSE event per step ("Maddələr və məhkəmə qərarları
axtarılır...") and then the result. If the client disconnects, the run is cancelled, so
abandoned requests stop spending tokens.

## Consequences
- The agent costs about three LLM calls per question (plus three per extra research round),
  against one for the baseline. The F5 eval measures whether that pays.
- The confidence threshold for abstaining is a parameter until it is calibrated on the eval.
- Not yet done: the planner's sub-questions are researched with the same tools. Intent-specific
  tools (for example the diff for temporal questions) are a later refinement.
