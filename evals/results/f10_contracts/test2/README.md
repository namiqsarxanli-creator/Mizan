# F10 contract analysis: the scored run on the second unseen set

The protocol is [`../PROTOCOL.md`](../PROTOCOL.md), amendment 3.

**Data:**
- **Set:** [`evals/datasets/contracts/test2`](../../../datasets/contracts/test2/). 10 contracts by
  the independent author, 228 clauses, 28 violations, 15 hard lawful clauses, 2 clean contracts
  and 1 injected instruction.
- **Labels:** checked before the run. One article number was corrected
  ([log](../../../datasets/contracts/test2/LABEL_CHANGES.md)).

**Run:**
- **When:** 2026-11-01, once.
- **Pipeline:** frozen at `4c1abf3`.
- **Model:** `gpt-oss-120b` on Groq, then on OpenRouter.
- **Cost:** 47 calls, 180k input and 51k output tokens.

Files: [`report.json`](report.json); [`llm_findings.jsonl`](llm_findings.jsonl), which holds
the findings and, for the first time, the dropped ones.

## Result: the DoD is not met
| layer | found | false alarms | missed | precision | recall | F1 | right article |
|---|---|---|---|---|---|---|---|
| rules | 0 / 28 | 3 | 28 | 0.00 | 0.00 | – | – |
| LLM alone | 14 / 28 | 4 | 14 | 0.78 | 0.50 | 0.61 | 0.64 |
| **combined** | **14 / 28** | **7** | **14** | **0.67** | **0.50** | **0.57** | **0.64** |

| requirement | threshold | result |
|---|---|---|
| precision | ≥ 0.80 | 0.67 ❌ |
| recall | ≥ 0.70 | 0.50 ❌ |
| right article | ≥ 0.80 | 0.64 ❌ |
| clauses parsed | ≥ 80% per contract | 100% (228/228) ✅ |

The injected instruction was caught and never sent to the model.

## Why
**1. Quote verification threw away 10 correct findings. It is the largest single loss.**

- 13 violations or risks were dropped as unverified. In 10 of them the model named the right
  violation and the right article, for example art. 52, 94, 164, 169, 174, 252 and 72.
- They were dropped because the model shortened its quote with "…", for example
  "hamilə və üç yaşına çatmamış uşağı olan qadınlara, … sınaq müddəti müəyyən edilməyən hal".
  The check looks for the quote verbatim, so any elision fails.
- The other 3 dropped findings were wrong; verification was right to drop them.
- Had the elided quotes been verified segment by segment, the LLM layer would have found
  about 24 of 28. That estimate is post-hoc; see below.

**2. The rules found nothing here and raised 3 false alarms.**

None of the 28 violations is of a kind the rules know: equal pay, recall from leave, two
sanctions for one offence, idle-time pay, union consent and so on. The false alarms:

| clause | why it fired |
|---|---|
| u04 2.1 | 08:30–17:30 with the hour 12:30–13:30 covered by a substitute: the rule did not recognise the lunch hour without the word "nahar". |
| u04 3.2 | A fine of one fifth of the monthly wage under a collective agreement is lawful (≤ 1/4). The rule knew only "dörddə bir", 1/4 and 25%, not "beşdə biri". |
| u09 1.5 | 10 days of probation in a four-month contract is lawful. The rule read the contract's length ("dörd aylıq"), stated in the same sentence, as the probation. |

The rules were tuned on the first test set. On a set built from different violations they
added nothing and cost precision. They work as a high-precision net for the limits they
encode, and only when the wording matches their patterns closely.

**3. Wrong article numbers.** In 5 of 14 hits the model cited a neighbouring article (138 for
137, 58 for 59, 74 for 79, 65 for 66, 61 for 162). The violation was right; the anchor was
off by one provision. This is what drags the right-article rate to 0.64.

## Assessment
- **Twice, the pipeline frozen before an unseen set failed the DoD.**
  - Recall was 0.28, then 0.50; this time precision fell to 0.67.
  - The model's judgement is better than the scores show: with the dropped findings counted,
    it saw most of the violations.
  - Two components cost it: verification that is too literal, and rules that are too brittle.
- **This is a negative result.** It stays the result of record. Changes made after reading it
  are post-hoc and reported as such.

## Post-hoc (2026-11-01): elided quotes, safer rules; this set is now development data
Changes after this run:

- **Quote verification accepts elided quotes.** A quote shortened with "…" passes when each
  segment, of at least 12 characters, occurs verbatim and in order in the cited source. The
  displayed quote is the statute's own words, joined with " … ". `/v1/ask` uses the same check.
- **The rules lost their 3 false alarms:**
  - the contract's own length is no longer read as the probation;
  - a short span inside the working day counts as its break;
  - a fine's share is read in any form ("beşdə biri", 1/5, 20%), and a fine the collective
    agreement provides for is not a forbidden deduction.

  No rule was added for this set's violation types. Encoding them would fit the rules to this
  set again; they are left to the LLM layer.

**Re-scoring.** The re-score used the recorded findings plus the dropped ones that pass the
new check. Dropped quotes were re-verified against the article text from the corpus, not the
exact chunk the model saw, so the result is approximate
([`posthoc.json`](posthoc.json)).

- **Recovered findings:** 8 of 13. 6 are true violations and 2 hit lawful clauses (u01 2.6 as a
  risk, u04 1.3 as a violation).

| layer | found | false alarms | precision | recall | right article |
|---|---|---|---|---|---|
| combined, post-hoc | 20 / 28 | 5 | 0.80 | 0.71 | 0.65 |

That is at the precision and recall thresholds, and still below the right-article threshold.
It is an estimate made after seeing the data. The fixed pipeline is measured once more on a
third unseen set ([`../../../datasets/contracts/test3/BRIEF.md`](../../../datasets/contracts/test3/BRIEF.md)).
The project committed in advance to closing F10 after that run, whatever the result.
