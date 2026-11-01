# F10 contract analysis: the final scored run (third unseen set)

The protocol is [`../PROTOCOL.md`](../PROTOCOL.md), amendment 4. **This is the last scored run
for F10**, as committed in advance.

**Data:**
- **Set:** [`evals/datasets/contracts/test3`](../../../datasets/contracts/test3/). 10 contracts,
  240 clauses, 28 violations (14 numeric, 14 needing reasoning), 16 hard lawful clauses and 2
  clean contracts.
- **Labels:** checked before the run. Two article numbers were corrected
  ([log](../../../datasets/contracts/test3/LABEL_CHANGES.md)).

**Run:**
- **When:** 2026-11-01, once.
- **Pipeline:** frozen at `1d94082`.
- **Model:** `gpt-oss-120b` on Groq, then on OpenRouter.
- **Cost:** 50 calls, 192k input and 57k output tokens.

## Result: the DoD is not met
| layer | found | false alarms | missed | precision | recall | F1 | right article |
|---|---|---|---|---|---|---|---|
| rules | 4 / 28 | 5 | 24 | 0.44 | 0.14 | 0.22 | 0.00 |
| LLM alone | 13 / 28 | 8 | 15 | 0.62 | 0.46 | 0.53 | 1.00 |
| **combined** | **17 / 28** | **12** | **11** | **0.59** | **0.61** | **0.60** | **0.76** |

| requirement | threshold | result |
|---|---|---|
| precision | ≥ 0.80 | 0.59 ❌ |
| recall | ≥ 0.70 | 0.61 ❌ |
| right article | ≥ 0.80 | 0.76 ❌ |
| clauses parsed | ≥ 80% per contract | 100% (240/240) ✅ |

**Where it worked.** Every one of the LLM's 13 hits cited the right article. Recall rose from
0.28 (first set) and 0.50 (second) to 0.61.

## Why it failed
**1. One wrong fact caused 9 of the 12 false alarms.**

- **The mistake.** w08 1.4 says the employee adopted a child "4 fevral 2026-cı il
  təvəllüdlü". The context extractor took the *child's* birth date for the employee's and
  concluded the employee was 0 years old.
- **The rules** then flagged a normal 40-hour week (art. 91) and 23 days of leave (art. 119).
- **The review prompt** stated "işçi 0 yaşındadır" as a fact about the contract. The model
  then called five unrelated clauses unlawful (art. 46: no employment under 15).
- **The lesson:** facts injected into a prompt are trusted more than the clauses themselves.
  An extracted fact needs a sanity check (whose birth date? an age of at least 14) before it
  is shown to a model or a rule.

**2. The rules misread three more clauses.**

| clause | what went wrong |
|---|---|
| w04 2.1 | "bir saatlıq yemək fasiləsi" was not recognised as a break, so the day counted as 9 hours. |
| w09 1.4 | "səkkiz aylıq" (the contract's length) was counted as probation. The filter only knew "… aylıq müqavilə". |
| w10 4.3 | "bir hissə azı 14 təqvim günü" (a part of split leave) was read as total leave of 14 days. |

These are the same family of errors as before: patterns keyed to phrasings.

**3. Five correct findings were dropped by quote verification.**

- Examples: art. 230, 198, 124 and the PPE article.
- The model still elided with "…" around short fragments ("İşçi … bir aylıq …"). It also
  opened a quote with "…" before a short segment, and the 12-character minimum per segment
  rejected those.

**4. Missed outright:** 6 violations, with no finding at all:

- the shortened day before a holiday (art. 108);
- sick leave counting towards service (art. 132);
- lighter work in pregnancy (art. 243);
- military call-up (art. 74);
- final settlement (art. 83);
- adoption leave (art. 126).

## F10 closes on a negative result
Three pipelines were frozen before three unseen sets by an independent author:

| set | precision | recall | right article |
|---|---|---|---|
| first (test) | 1.00 | 0.28 | 0.88 |
| second (test2) | 0.67 | 0.50 | 0.64 |
| third (test3) | 0.59 | 0.61 | 0.76 |

**Finding.** The LLM layer's legal judgement is the strong part: right articles on all hits
in the third run, and recall rising each round. Deterministic rules keyed to phrasings did
not generalise. On unseen drafting they cost more precision than they added recall.

**Decision (ADR-0013, revised).**

- **Rules are demoted** from a detection layer to a narrow set of guarded numeric checks, each
  with a sanity test on its extracted inputs.
- **No unchecked fact goes into the prompt.**
- **Detection is the LLM's job.** It runs with lenient but faithful quote verification and the
  rules as hints.
- **The product ships the feature as a research aid.** The disclaimer stays, and every
  finding carries the statute's words for a person to check.
- **No accuracy claim is made.** None is supported.
