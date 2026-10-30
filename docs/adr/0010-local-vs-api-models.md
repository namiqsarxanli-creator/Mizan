# ADR-0010: Which tasks go to a local model and which to the API

- **Status:** Accepted
- **Date:** 2026-10-30
- **Phase:** F8

## Context
F8 asked whether narrow, frequent LLM tasks can move from the API (Groq) to a small model of our
own. The first candidate was claim verification: label one claim against one source as
`supported`, `contradicted` or `not_enough_info`. It runs once per cited claim of every answer,
so it is the most frequent LLM call after drafting.

The pre-registered evaluation ([`evals/results/f8_verifier`](../../evals/results/f8_verifier/README.md))
compared the two verifiers on 114 claims from 31 unseen articles:

| | API: `gpt-oss-20b` on Groq | local: Qwen3.5-2B + LoRA on a T4 |
|---|---|---|
| macro-F1 | 0.951 | 0.935 (98.4% retained; gate ≥ 95%) |
| cost per claim | $9.5e-5 | $1.3e-5 at $0.22/h (7.3×; gate ≥ 5×) |
| latency per claim | p50 1.59 s | 214 ms |

Both gates pass, with these caveats:

- The retention interval is [0.91, 1.05]. A bootstrap over articles leaves 18% of resamples
  below 0.95.
- The test claims share the training claims' synthetic style.
- The cost holds only while the GPU is busy. A dedicated $0.22/h GPU costs about $160 a month.
  At $9.5e-5 per claim, that equals about 1.7M API verifications a month, a sustained 0.6
  claims/s.

Mizan's traffic today is orders of magnitude below that. The serving machine has no GPU
(ADR-0009).

The other two F8 candidates, a citation extractor and a query rewriter, were not trained. The
verifier was done first because it is the most frequent call and has gold labels by
construction. The other two stay on the API until each has its own pre-registered gate and
passes it.

## Options considered
| Option | Pros | Cons |
|---|---|---|
| A. Everything stays on the API | no GPU to run; quality known | the per-claim cost and 1.6 s latency stay |
| B. Verification moves to the local model now | 7× cheaper per claim, 7× faster | an idle dedicated GPU costs more than the API at current traffic; quality on real claims is unmeasured |
| C. API by default; local verifier qualified, switched on by traffic and evidence | pays for a GPU only when it pays back; the switch rests on evidence | needs a serving path and a shadow run before the switch |
| D. Local verifier on a scale-to-zero serverless GPU | no idle cost | cold starts of tens of seconds: worse than the API for interactive answers |

## Decision
**Option C.** The rule for any task:

1. **Local model** when the task is narrow, has a fixed label set or schema, and is
   high-volume. It must also pass a pre-registered gate on held-out data: quality ≥ 95% of the
   API, cost ≤ 1/5.
2. **API** for open-ended generation: drafting, planning, the judge, and anything whose output is
   read by a person. It also covers any task whose volume does not keep a GPU busy.

Claim verification is the one task that qualifies on (1). It stays on the API until both hold:

- Sustained verification traffic exceeds the break-even of about 0.6 claims/s, or a GPU is
  already paid for by other work.
- A shadow run (F9 `ShadowRunner`, the same mechanism used for `gpt-oss-20b` vs `-120b`) on
  real traffic agrees with the API verifier at least as often as the API agrees with the gold
  labels. That run also covers the pre-registered secondary check that F8 did not run.

The switch is a gateway routing rule for `task=verify`, with the API as the fallback.

## Consequences
- **Easier:** no GPU to operate now. The trained verifier, its training code (`mizan-train`)
  and the free-GPU notebook (`infra/gpu/verifier_notebook.ipynb`) make retraining an hour on
  a free T4.
- **Easier:** the gate and the break-even arithmetic are written down, so the decision to switch
  is mechanical when the traffic arrives.
- **Harder, accepted:** the 7× saving is not realised today.
- **Harder, accepted:** the adapter (70 MB) is not in the repository. Serving it means
  retraining, or keeping a copy outside git, for example on the Hugging Face Hub at F11 with
  approval.

**How we will know it was wrong:**
- Verification volume grows past break-even and API spend on `verify` dominates the bill.
  Switch to the local model.
- The shadow run shows the local model disagreeing with the API on real claims well beyond
  the test error rate. The synthetic-style caveat was the real limit; retrain on labelled real
  claims from the review queue (F9) before trying again.
