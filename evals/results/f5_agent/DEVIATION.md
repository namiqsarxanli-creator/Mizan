# F5 agent eval — deviation from the pre-registration (2026-10-27)

Written and committed **before** the rerun it describes.

## What happened

The harness reruns a record only when it carries an `error`. The two systems fail differently
when the LLM provider is unavailable:

- **baseline** raises `AnswerUnavailableError`. The record is marked as an error and rerun.
  Nine baseline records were rerun this way after Groq's daily token limit reset.
- **agent** degrades gracefully. A drafter call that fails or times out (`drafter_failed`)
  ends the loop with no draft, and the agent returns its normal abstention text. The record
  looks like a legitimate abstention and is never rerun.

The logs of 2026-10-26 show `drafter_failed` for cd02–cd11 between 15:27 and 15:43 UTC, during
the window when Groq reported the `gpt-oss-120b` tokens-per-day limit as exhausted (the drafter
model). Earlier questions show 429 retries of 11–39 s against the 90 s step timeout. These
abstentions measure the free tier's quota, not the agent.

## The rule (mechanical, outcome-blind)

An agent record is an **infrastructure failure** when it abstained with `ABSTAIN_TEXT` and has
**no claims**, i.e. the drafter never returned a draft. The rule does not look at
`correct`, the gold, or the baseline.

On 2026-10-27 it selects 17 records: q031, q103, q105, q138, q181, q182, q186 and cd02–cd11.
They are re-marked as errors (append-only: the original records stay in `records.jsonl`, and a
copy with `error` set is appended) and rerun with the same pre-registered agent code (commit
4b4dee2) and the same judges.

## What is reported

1. **As pre-registered, unchanged:** the result before this rerun. `correct` Δ −0.255
   [−0.39, −0.12]; DoD item 3 fails. The summary at that point is kept as
   `summary_preregistered.json`.
2. **After the infrastructure rerun:** the same metrics, labelled as such.

If the rerun reproduces abstentions with no draft while the provider is healthy (no 429 or
token-limit errors in the log for that question), those count as the agent's own failures
(the 90 s step timeout is part of the system) and stay as they are.
