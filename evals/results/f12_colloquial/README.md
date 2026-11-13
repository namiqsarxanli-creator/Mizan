# F12: everyday wording (result of record)

Protocol: [`PROTOCOL.md`](PROTOCOL.md), committed (`13122dc`) after the held-out set was frozen
(`46bee2e`) and before any variant ran on it. Full tables: [`report.md`](report.md);
diagnostic: [`context.md`](context.md).

## Results

| Set | V0 baseline | V1 refs | V2 rewrite | V3 multi-query |
|---|---|---|---|---|
| Held-out colloquial (n = 60), Recall@5 | 0.933 | 0.933 | 0.900 | 0.933 |
| Δ vs V0 [95% CI] | - | +0.000 | -0.033 [-0.083, +0.000] | +0.000 [-0.050, +0.050] |
| v1 (n = 185), Recall@5 | 0.946 | 0.951 | 0.938 | 0.943 |
| Δ vs V0 [95% CI] | - | +0.005 [+0.000, +0.016] | -0.008 [-0.041, +0.024] | -0.003 [-0.022, +0.019] |

## Decisions (by the pre-registered rules)
- **Refs: on** (`retrieval.article_refs = true`). No Hit@5 loss on either set; fixes `q121`
  ("69-cu maddəsi nə barədədir?"). The held-out set names no articles, so it cannot show a gain.
- **Rewrite: stays off.** Neither V2 nor V3 has a held-out gain with a CI above 0; V2 is lower
  (two questions lost, none gained). On v1, V2 fixes four questions and breaks five. This is
  the second negative result for rewriting (F2 was the first).

## What the held-out set says about the original worry
The worry was that everyday wording defeats retrieval. On questions written by another model
family in a worker's words, without the Code's terms, the served configuration finds the right
article in the top five 93% of the time (v1: 95%). The gap exists but is small, and rewriting
does not close it.

## Why `q001` still fails: context packing, not retrieval
Search ranks Art. 69 fifth for `q001`. `/v1/ask` expands every hit to its whole article and
cuts the context at 9000 characters; Art. 77 alone is ~6200, so Art. 69 never reaches the
model and the answer says the sources do not cover the question. Measured without LLM calls
(`context.py`): the gold article is retrieved but lost to the budget in **3 of 245** questions
(`q001`, `q067`, `c002`). A fix (keep only the matched parts of long articles) would help at
most ~1% of questions and changes what the model reads for every question, so it needs the F3
generation eval to justify it; it is not switched on here.

## Limits
- 60 held-out questions: a difference smaller than ~0.05 cannot be resolved.
- Labels are the article each question was written from; another article may also answer it.
- Seven v1 questions and one held-out question got no restatement (the rewrite model returned
  nothing; as in serving, the original question is used). Rewrite variants equal V0 on them.
- The held-out set has now been used for this decision; it is no longer held out.
