# F5: multi-agent research vs single-shot RAG

**Verdict: DoD items 1 and 2 pass, item 3 fails.** The agent cites far more accurately, but it
is *less* often correct than the single-shot baseline. The baseline stays the default `/v1/ask`
path. The agent is served at `/v1/agent` as an opt-in mode for users who want verifiable quotes.

The protocol is in [PREREGISTRATION.md](PREREGISTRATION.md) (written before the run) and the one
deviation is in [DEVIATION.md](DEVIATION.md) (written before the rerun it describes). Per-question
records are in `records.jsonl`, an append-only log in which the last record per (system,
question) counts.

## Results (51 questions, same judges for both systems)

| | baseline | agent |
|---|---|---|
| correct | **0.843** [0.75, 0.94] | 0.725 [0.60, 0.84] |
| coverage (answered, in scope) | 1.00 | 0.98 |
| selective accuracy (on answered) | **0.833** | 0.723 |
| faithfulness (on answered) | 0.901 | 0.912 [0.85, 0.96] |
| citation accuracy (verbatim quotes) | 0.644 | **0.920** |
| p50 latency | **2.8 s** | 19.1 s |
| mean tokens | **4 238** | ≈ 5 600 |

Paired `correct`, agent − baseline: **−0.118 [−0.235, −0.02]** (significant, against the agent).

| DoD (pre-registered) | target | result | |
|---|---|---|---|
| 1. agent faithfulness | ≥ 0.90 | 0.912 | ✅ |
| 2. agent citation accuracy | ≥ 0.90 | 0.920 | ✅ |
| 3. agent better on `correct` | lower bound > 0 | −0.235 | ❌ |

### Before the deviation (as first computed)

The agent turned provider failures into abstentions: drafter timeouts during 429 retries, and
the `gpt-oss-120b` daily token limit. The harness reruns only records marked as errors, so these
were never rerun. The baseline's failures were rerun.

On those numbers `correct` was 0.588, coverage 0.708, and Δ **−0.255 [−0.39, −0.12]**
(`summary_preregistered.json`). The outcome-blind rerun of the 17 records where the drafter never
returned leaves the verdict unchanged. It halves the gap: about half of the first-run deficit
was the free tier's quota, not the agent.

## Where the agent loses

The 51 paired outcomes:

| | baseline right | baseline wrong |
|---|---|---|
| **agent right** | 36 | 1 |
| **agent wrong** | 7 | 7 |

The seven questions the agent alone gets wrong, by judge reason:

1. **Dropped conditions** (q031, q138). The answer is right in substance but omits a condition
   that changes the conclusion: a medical examination requirement, the written-agreement clause.
   The drafter compresses more evidence into shorter claims, and conditions are what gets lost.
2. **Added requirements** (q049, q142). The answer adds a prohibited-work category, or a 6-month
   service requirement for maternity leave, that the Code does not contain. Confidence was
   0.87 and 1.0.
   - The verifier checks each claim *against* the sources, but nothing checks that the claims
     *cover only* what the sources say. An extra clause attached to a supported claim passes.
3. **Constitutional Court decisions underused** (cd02, cd05, cd08). The baseline always puts
   the decisions in its context. The agent's researcher retrieves them, but the drafter weighs
   them like any other evidence and follows the article text where the Court's interpretation
   overrides it.

## What this means for the design

- The single-shot pipeline is strong here because retrieval is already good: Recall@5 0.95 on
  v1. Decomposition adds the most when retrieval is the bottleneck, and here it is not.
- Verified quotes are the agent's real contribution: 0.92 vs 0.64. A cheaper way to get most of
  that is to run the *verifier* on the baseline's answer (post-hoc citation checking) without the
  planner and drafter. That is one LLM call instead of five, and the baseline's accuracy is kept.
- Concrete fixes for a future agent version, each to be tested on a fresh question sample, never
  on these 51:
  1. a coverage check in the verifier: "does the answer state anything the quote does not?";
  2. decisions pinned in the drafter's prompt above article text, as the baseline does;
  3. drafter step timeout above the provider's retry-after, with provider failures surfaced
     as errors rather than abstentions;
  4. the planner's empty plan accepted. On `main`, the orchestrator already degrades to the
     question itself.

## Known limitations

- The F3 judge (`gpt-oss-20b`) is not calibrated against human labels (κ pending).
- The court-decision subset is 11 questions; per-type differences are not significant on their own.
- The free tier's rate limits shaped latency, and through timeouts outcomes too. See DEVIATION.md.
