# Demo script (3–5 minutes)

A screen recording with voice-over. Each scene lists what to show, what to say and the exact
command or input. Record at 1920×1080 with the terminal font at 16 pt or larger. Keep the
stack running before recording, so models are loaded.

Prerequisites: a Groq key in `.env` and Docker Desktop running. Run `run_mizan.py` (in PyCharm: right-click,
Run; it needs only the standard library, so any Python 3 works): it starts the stack, waits for
the API on `http://127.0.0.1:18000` and opens the question page at `/`. The first start downloads the embedding model (about 2 GB).

## 1. The problem (0:00–0:30)
**Show:** the README title and the architecture diagram.

**Say:** "Azerbaijani labour law changes often, and people ask about it in everyday language.
Mizan answers with the exact article in force on a date and quotes it word for word, and every
quote is checked by code, not by trust."

## 2. A question, with citations (0:30–1:15)
**Run:**
```bash
curl -s -X POST http://127.0.0.1:18000/v1/ask -H 'content-type: application/json' \
  -d '{"question": "Hamilə işçini ixtisar etmək olarmı?"}' | jq
```
Or ask it on the question page at `http://127.0.0.1:18000/` (example questions are one click).

**Point at:** the answer; `citations[].quote` (the statute's own words); the article key
`art:79`.

**Say:** "A quote that does not occur in the article is dropped. The model cannot invent a
citation."

## 3. The law on a past date (1:15–1:45)
Ask the same question with `"as_of": "2010-06-01"`, or a temporal question from
`evals/datasets/v1/questions.jsonl`.

**Say:** "Every version since 1999 is stored. The answer uses the text in force on the date
asked about."

## 4. Contract check (1:45–2:45)
**Open:** `http://127.0.0.1:18000/contracts` and upload `evals/datasets/contracts/v1/c04.txt`.

**Point at:**
- the red clauses, with the article and the quoted provision;
- the purple clause: hidden instructions to an AI, reported and not obeyed (`c09.txt` shows
  one);
- the disclaimer.

**Say, honestly:** "On contracts written by someone else, it finds about 60% of violations.
That is below our own bar, so it is a research aid, not a lawyer."

## 5. Measured, not claimed (2:45–3:45)
**Show:** the README results table, then `docs/REPORT.md` section 4.4: the multi-agent system
lost to the simple baseline.

**Say:** "Every component was measured against a baseline with thresholds set in advance.
Some failed, and they are in the report: the agent, two fine-tunes, the cache and contract
analysis."

## 6. Engineering (3:45–4:30)
**Show:**
- CI on GitHub (lint, strict typing, about 540 tests, eval gates, dependency audit);
- `evals/results/f11_load/README.md` (0 errors, where the bottleneck is);
- `docs/security/owasp-llm-top10.md`.

**Say:** "Auth, tenant isolation in the database, a prompt-injection gate in CI, load tested,
and every decision recorded as an ADR."

## 7. Close (4:30–4:45)
"Mizan: a measured legal research aid for Azerbaijani labour law. The code, the eval sets and
the report are linked below."

## GIF for the README (10–15 s)
Record scene 4 only, from upload to the red clauses appearing, at 800 px wide. Save it as
`docs/demo.gif` and link it under the README's Architecture section.
