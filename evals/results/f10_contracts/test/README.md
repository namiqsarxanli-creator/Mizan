# F10 contract analysis: the scored test run (independent-author set)

The protocol is [`../PROTOCOL.md`](../PROTOCOL.md). Amendment 1 covers the independent
author; amendment 2 covers the provider chain.

**Data:**
- **Set:** [`evals/datasets/contracts/test`](../../../datasets/contracts/test/). 10 contracts by
  another model, 200 clauses, 29 violations, 13 hard lawful clauses and 2 clean contracts.
- **Labels:** checked against the Code before the run. No changes were needed
  ([log](../../../datasets/contracts/test/LABEL_CHANGES.md)).

**Run:**
- **When:** 2026-10-31, once.
- **Pipeline:** frozen at `eb4257f`.
- **Model:** `gpt-oss-120b` on Groq, then on OpenRouter once Groq's daily limit was spent.
- **Cost:** 40 calls, 155k input and 43k output tokens.

Files: [`report.json`](report.json), [`llm_findings.jsonl`](llm_findings.jsonl).

## Result: the DoD is not met
| layer | found | false alarms | missed | precision | recall | F1 | right article |
|---|---|---|---|---|---|---|---|
| rules | 4 / 29 | 0 | 25 | 1.00 | 0.14 | 0.24 | 0.75 |
| LLM alone | 6 / 29 | 0 | 23 | 1.00 | 0.21 | 0.34 | 1.00 |
| **combined** | **8 / 29** | **0** | **21** | **1.00** | **0.28** | **0.43** | **0.88** |

| requirement | threshold | result |
|---|---|---|
| precision | ≥ 0.80 | **1.00** ✅ |
| recall | ≥ 0.70 | **0.28** ❌ |
| right article | ≥ 0.80 | **0.88** ✅ |
| clauses parsed | ≥ 80% per contract | **100%** (200/200) ✅ |

**What the system flags, it gets right.** It raised no violation on any lawful clause,
including the 13 hard ones and the 2 clean contracts. One `risk` on a lawful clause.

**It misses most violations.** On the development set the same pipeline scored precision
1.00 and recall 1.00. The difference measures how far "works on what it was built for" is from
"works".

## Why it missed (21 violations)
**1. The rules match their author's phrasing, not the law (most of the 25 rule misses).**

| what the contract said | what the rule expected |
|---|---|
| a birth date ("doğum tarixi 10 dekabr 2009") | an age ("(17 yaş)") |
| a date range ("1 iyun … 31 iyul 2026") | a term ("dörd ay müddətinə") |
| "hər gün doqquz saat", or "08:00–18:00" with an hour's lunch | "gündə N saat" |
| "ərizəsi yalnız üç ay əvvəl verildikdə qəbul edilir" | "xəbərdar … İşəgötürəni" |
| "iki ardıcıl günün hər birində üç saat" | "iki iş günü ərzində N saat" |

So the minors' probation (art. 52), the probation in a two-month contract (art. 52), the
45-hour weeks (art. 89), the 3-month notice (art. 69) and the overtime limit (art. 100) were
all missed.

**2. The rules have no notion of some protected groups and jobs.**

- **Pregnancy** (art. 242, 79) is not a contract fact the rules track.
- **Teachers** (art. 118, 56 days) and **minors' leave** (art. 119, 35 days) are not in the
  long-leave table.
- **Minors' night work** (art. 254) has no rule.

**3. The LLM layer found 6 of 29.**

- It found the plain cases: 6-month probation, dollar wages, a dismissal ground outside
  art. 70, a forced resignation, 14 days of leave, a leave waiver.
- It missed the pregnancy clauses, the fines (art. 186), the waivers of rights (art. 9), the
  union dismissal (art. 16/79) and the unpaid leave compensation (art. 144).
- **16 violations or risks were dropped because their quote did not verify.** Their content
  was not logged, so it is unknown how many were correct findings with a misquoted source.
  This is the largest unexplained loss.
- Retrieval per clause (3 articles each, 9,000 characters per batch) also may not have put
  the right article in front of the model, for example art. 242 for night work in pregnancy.

## What happens next (post-hoc; does not replace this result)
1. **Log every dropped finding,** with its quote and the source it cited, to measure how many
   correct findings the verifier discards and why.
2. **Rules on facts, not phrases:**
   - age from a birth date;
   - term from a date range;
   - daily hours from "hər gün N saat" and from start/end times minus lunch;
   - notice periods in any phrasing;
   - pregnancy, teachers and minors as contract facts, with their limits (art. 118, 119,
     242, 254).
3. **Retrieval for contracts:**
   - add the facts (minor, pregnant, teacher, term) to the retrieval query;
   - a fixed set of always-relevant articles per topic (working time, leave, discipline,
     dismissal protection).
4. **A second independent test set,** written by the other author before those fixes are
   scored, so the fixed pipeline is also measured once on contracts it has not seen. This set
   is now development data.
