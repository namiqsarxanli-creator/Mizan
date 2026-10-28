# F8 local verifier — pre-registration

Written and committed **before** any verification data was generated, any model was trained, or
the API verifier was scored on the test split.

## Task

The production verifier (`mizan_agents.agents.verify`, `gpt-oss-20b` on Groq) reads a legal
claim and the source text it cites. It returns `supported`, `contradicted` or
`not_enough_info`. The local model does the same task, one claim per request.

## Data (`mizan-train verify-synth`)

For every Labour Code article in force, an LLM writes four claims with labels known by
construction:

| kind | label | what it is |
|---|---|---|
| `restated` | supported | a rule of the article in other words |
| `altered` | contradicted | the same rule with a number, period, party or condition changed |
| `absent` | not_enough_info | a plausible labour-law claim that this article does not address |
| `extended` | not_enough_info | a true rule of the article **plus** a requirement the article does not state |

`extended` is the F5 failure mode: a supported claim with an extra clause attached.

- **Validation:** an independent judge from a different model family labels each claim. Claims
  where its label differs from the intended one are dropped.
- **Split:** `article_split` by article, the same function and seed as F7. Train articles, val
  articles and **test articles are disjoint**.

## Systems, scored on the same test claims

| name | model |
|---|---|
| `api` | `gpt-oss-20b` (Groq) with the production `VERIFIER_SYSTEM` prompt, one claim per call |
| `local` | `Qwen/Qwen3.5-2B` + LoRA, trained on train claims; checkpoint chosen by val macro-F1 |

## Primary outcomes (the DoD)

1. **Quality:** macro-F1 over the three labels on the test claims.
   **Pass if F1(local) ≥ 0.95 × F1(api).**
2. **Cost:** cost per verified claim, measured on the same test claims.
   - api: the Groq list price for its measured tokens.
   - local: the GPU's hourly price ÷ claims per hour at measured throughput.
   - **Pass if cost(local) ≤ cost(api) / 5.**

## Secondary (reported, not gating)

- Per-kind accuracy, `extended` above all; the confusion matrix for both systems.
- p50 latency per claim.
- Agreement of `local` with `api` on the real claims of the F5 run (no gold).

## Fallback, stated in advance

If `Qwen/Qwen3.5-2B` cannot be trained or served with the available stack, the base model is
`Qwen/Qwen2.5-1.5B-Instruct` and the switch is recorded here. No other model is tried on the
test split.
