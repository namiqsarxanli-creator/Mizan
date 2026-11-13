# F12: everyday wording vs the Code's wording (pre-registration)

Written and committed before any variant below is run on the new set.

## Problem
Retrieval misses questions whose wording differs from the Code's. The most visible case:
"İşdən çıxmaq istəyirəm, işəgötürənə neçə gün əvvəl xəbər verməliyəm?" (v1 `q001`) does not
bring Art. 69 ("əmək müqaviləsinə işçinin təşəbbüsü ilə xitam") into the first results, and the
answer says the sources do not cover it. In the F3 run, three of the five dense misses were about
Art. 69, including `q121` ("Əmək Məcəlləsinin 69-cu maddəsi nə barədədir?"), which names the
article.

## Data
- **Held-out: `evals/datasets/colloquial/questions.jsonl`**: 60 questions, one per randomly
  sampled article (seed 2026), written by `gemini-2.5-flash` from a provision in force today,
  in a worker's or employer's words, told not to use the provision's terms. Frozen in commit
  `46bee2e` before this protocol. Labels are the source article; recall is a lower bound.
- **Regression set: `evals/datasets/v1/questions.jsonl`**: the 185 answerable v1 questions.
  Every earlier retrieval decision was made on it, so it is not held out; it guards against
  regressions.

## Variants (all dense `bge-m3`, article chunks: the serving configuration)
- **V0**: baseline, as served.
- **V1 refs**: V0, plus explicit article references in the question ("69-cu maddə",
  "maddə 69", "69-cu maddənin 2-ci hissəsi") put the named article's best-matching provisions
  first. Only affects questions that name an article.
- **V2 rewrite**: V0 on the question expanded by the existing LLM rewrite
  (`openai/gpt-oss-20b`, `question + "\n" + restatement`). F2 found no significant gain on v0.
- **V3 multi-query**: V0 run twice, on the question and on the restatement alone, fused by
  reciprocal rank fusion (k = 60) at chunk level. Restatements are the same as V2's.

## Metrics
Hit@5 and Recall@5 at article level, MRR@10. 95% CIs by paired bootstrap over questions
(10 000 resamples, seed 0) for the difference to V0.

## Decision rules (fixed now)
- **Rewrite (V2 or V3)** is switched on for `/v1/ask` only if, on the held-out set, its
  Recall@5 gain over V0 has a 95% CI above 0, **and** on v1 its Recall@5 is not lower than
  V0's by more than 0.01. If both qualify, the one with the larger held-out gain. Cost:
  one extra small-model call per question (~1.5 s, F2).
- **Refs (V1)** is switched on if it lowers neither set's Hit@5 (point estimate). It is a
  correctness rule (a user who names an article must get that article), so it does not need a
  significant gain; v1 has 20 `exact_term` questions to show its effect.
- The held-out set is used once for this decision. Whatever the result, it is reported here.

## Amendment 1 (before any run): focused context for long articles

Added after the result above showed `q001`'s article retrieved but cut by the context budget.
Committed before the variant was run on any set.

- **Variant F2500**: as served, but an expanded article longer than 2500 characters keeps only
  the parts search matched, one part on each side, and the breadcrumb (`focus_article`). The
  threshold is fixed here and will not be tuned on these results.
- **Step 1, offline (no LLM)**: share of questions whose gold article reaches the answering
  model (`context.py`), on the colloquial set and v1, as served vs F2500.
- **Step 2, answers**: the served answer model and the F3 judge on every question whose context
  differs between the two. Free-tier limits cap this at about 30 questions (two answers and two
  judgements each). If more differ, a random 30 (seed 0), plus every question where step 1 says
  the gold article is lost as served.
- **Rule**: F2500 goes on if step 1 is not lower on either set **and** in step 2 the judge's
  correct rate is not lower than as served (point estimate). Either way, the result is reported.
- **Known limit**: the judge's correct rate is lenient (F3: 0.914, with weak agreement with a
  second model). Step 2 is a small, noisy check against harm, not a precise estimate.
