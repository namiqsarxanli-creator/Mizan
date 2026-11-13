# F12: follow-up questions (result of record)

Set: [`evals/datasets/followups`](../../datasets/followups/), 20 two-turn conversations written by
`gemini-2.5-flash`, frozen (`508c922`) before the script and its decision rule were committed
and run. Full table: [`report.md`](report.md).

| How the follow-up is searched | Hit@5 |
|---|---|
| as written ("Bəs ona nə qədər tez-tez baxılır?") | 0.45 [0.25, 0.65] |
| previous question + follow-up (no LLM) | **1.00** |
| restated by the rewrite model (`gpt-oss-20b`) | 0.80 [0.65, 0.95] |

## Decision
The rule fixed before running: restating stays on only if it beats "as written" (it does,
+0.35 [+0.15, +0.55]) **and** is not below concatenation (it is, −0.20 [−0.40, −0.05]). So
**`/v1/ask` reads a follow-up together with the previous question** and makes no extra LLM
call. Restating remains available (`MIZAN_LLM__CONDENSE_FOLLOWUPS=true`) but is off. One
restatement came back in English (`f018`), another dropped the subject (`f001`).

## Limits
- **Biased towards concatenation.** Every follow-up here asks about the *same* article as
  the first question, so adding the first question can only help. A follow-up that changes
  topic ("Bəs məzuniyyət neçə gündür?") is not in the set; for it the previous question is
  noise, and a restatement might do better. That case is unmeasured.
- 20 conversations; retrieval only (Hit@5), not the answers.
- Each first question was answered by the running API, as the question page does; those
  answers are cached in `first_answers.json`.
