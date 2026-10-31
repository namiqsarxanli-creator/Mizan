# F10 contract analysis: results on the synthetic development set

- **Design:** [ADR-0013](../../../docs/adr/0013-contract-analysis.md).
- **Test protocol for real contracts:** [`PROTOCOL.md`](PROTOCOL.md).

Files:

- [`report.json`](report.json): all metrics;
- [`llm_findings.jsonl`](llm_findings.jsonl): every LLM finding with its verified quote.

**This is the development set.** One author wrote the contracts, the labels, the rules and
the prompt. These numbers show what each layer does on cases built for it. They are not a
generalisation claim; the real-contract test is.

## Set
- [12 synthetic contracts](../../datasets/contracts/build.py) on the Labour Code's model form
  (annex 1), one per sector.
- 256 clauses: 20 planted violations, 1 injected instruction to the model, and lawful clauses,
  some deliberately suspicious-looking.
- **Run:** 2026-10-31, `openai/gpt-oss-120b` on Groq, production settings (batch 6, 9,000
  characters of sources).
- **Coverage:** Groq's free daily token limit stopped the run after 9 of 12 contracts
  (c01–c09: 193 clauses, 17 violations). c10–c12 resume with the same command when the limit
  resets. The numbers below are for the 9 contracts.

## Results (9 contracts, clause level, positive = violation)
| layer | found | false alarms | missed | precision | recall | F1 | right article |
|---|---|---|---|---|---|---|---|
| rules | 17 / 17 | 0 | 0 | 1.00 | 1.00 | 1.00 | 1.00 |
| LLM alone (every clause) | 9 / 17 | 6 | 8 | 0.60 | 0.53 | 0.56 | 0.89 |
| **combined (what the product reports)** | **17 / 17** | **6** | **0** | **0.74** | **1.00** | **0.85** | **1.00** |

- **Injected instruction:** caught by the screen (1/1); never sent to the model.
- **LLM cost:** 53 calls, 173k input and 48k output tokens, about 25k tokens per contract.
  Ten violations or risks were dropped because their quote did not occur in the cited article.
  The verification is doing work.

## What the numbers say
**1. The LLM alone is not good enough, and the rules are why the product works.**

- It found the obvious cases: 6-month probation, 48-hour week, 14 days of leave, wages in
  dollars, 3-month resignation notice, overtime replaced by rest days. It also found one
  conditional case: a 1-month probation in a 4-month contract.
- It missed 8 violations:
  - overtime at 1.5x;
  - two overtime limits (4 h a day; 8 h in two days);
  - dismissal "at any time, without reason or notice";
  - 21 days of leave for a doctor (the law says 30);
  - dismissal of a pregnant employee;
  - 50% of wages in kind;
  - a waiver of severance pay.
- Most of these need a number compared with a limit, or a provision the retriever did not put
  in the batch's sources. That is the case for ADR-0013's split: rules for limits, the LLM for
  the rest.

**2. All 6 LLM false alarms are one clause.**

- Clause 8.1 of the model form: "the contract may be terminated on the grounds of articles 68,
  69, 70, 73, 74 and 75".
- The model read article 76 as "termination on one ground only" and flagged the clause in 6 of
  the 9 contracts. The quote was real; the reading was wrong.
- The clause is the Code's own template wording, so it cannot contradict the Code.
- This is the next fix, on dev: clauses that reproduce the model form verbatim are marked
  `ok` without a model call. Without the fix, combined precision would be 0.74. That is below
  the 0.80 the real-contract test requires ([`PROTOCOL.md`](PROTOCOL.md)), so it is fixed
  before the freeze.

**3. Risks on lawful clauses: 2.**

- "The probation clause does not mention the 3-day notice of art. 53."
- "The workplace address required by art. 43 is not stated."
- Both are reasonable prompts to look, not errors. They are reported apart from violations.

## Reproduce
```bash
uv run mizan-eval contracts --gate               # rules only, offline (CI)
uv run mizan-eval contracts --llm --pace 35      # + LLM review; needs the stack and a Groq key
```
The LLM run appends one row per finished contract to `llm_findings.jsonl` and resumes from
there. A contract with an unanswered batch is not recorded.
