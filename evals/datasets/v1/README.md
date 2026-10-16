# Evaluation dataset v1 (silver)

200 questions about the Azerbaijani Labour Code (e-qanun id 46943). Supersedes v0 for
generation evaluation; v0 (its first 70 questions, unchanged) stays the retrieval gate's set so
the gate's baseline remains comparable across F2 and F3.

| Type | n | What it tests | Correct behaviour |
|---|---|---|---|
| `single` | 101 | colloquial question, one article | cite and apply that article |
| `multi` | 35 | needs two articles | combine both |
| `exact_term` | 20 | statutory wording or article numbers ("70-ci maddə") | find the named provision |
| `temporal` | 19 | asked about a past date (2000-2024) | answer from the text in force then |
| `no_answer` | 15 | outside the Labour Code (tax, visas, inheritance...) | say the sources do not cover it |
| `false_premise` | 10 | built on a wrong rule ("probation can be a year?") | reject the premise, give the rule |

Fields: `id`, `question`, `relevant` (article keys; empty for `no_answer`), `type`, optional
`as_of` (default: today).

## How labels were checked
- Every gold article was resolved against the stored corpus **on the question's date**
  (temporal questions see the historical wording, e.g. "ləğv edilməsi" before 2025).
- Every false-premise question's correcting fact was searched for in the gold article text.
  This rejected one draft question (q197: no article states that collective agreements must be
  written), which was replaced by one the text supports (Art. 25: 10 calendar days).

## Limits
- **Silver:** written by an LLM (Claude) and checked mechanically, not yet reviewed by a lawyer.
  The F3 human labelling (`mizan-eval label`) is the first human pass; a lawyer review remains
  the path to a gold set.
- `relevant` lists the articles a lawyer would cite first; recall against it is a lower bound.
