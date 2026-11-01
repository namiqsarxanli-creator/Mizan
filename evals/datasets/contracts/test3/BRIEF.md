# Brief: a third test set of Azerbaijani employment contracts (for Codex)

You are writing a **held-out test set** for a system that checks employment contracts against
the Labour Code of the Republic of Azerbaijan (Əmək Məcəlləsi). The system was built by
someone else. Write it **without reading anything else in this repository**: not
`packages/`, not `evals/datasets/contracts/v1`, **not `evals/datasets/contracts/test` or `test2`**
(earlier sets), not `labels.jsonl` outside this folder, not `evals/results`. The point is that your wording and your choice of problems are independent of
the system's. Use only your own knowledge of Azerbaijani labour law and of how real contracts
are drafted.

## What to produce
All files go in `evals/datasets/contracts/test3/`:

1. **`contracts/w01.txt` … `contracts/w10.txt`**: ten employment contracts in Azerbaijani,
   UTF-8, plain text.
2. **`labels.jsonl`**: one JSON line for **every** clause of every contract.
3. **`NOTES.md`**: one paragraph per contract saying what you planted and why. Mention any
   label you were unsure of.

## The contracts
- **Realistic.** Write them as an HR department or a small business would. Vary the length (15
  to 40 clauses), the structure, the sector and the employer type:
  - sectors such as IT, retail, a factory, a hospital, a school, transport, hospitality,
    construction, a bank, a call centre or agriculture;
  - employer types such as an LLC, a joint-stock company, an individual entrepreneur or a
    public body;
  - both open-ended and fixed-term contracts.
- **Your own drafting.** Do **not** copy the Code's model form (annex 1) clause by clause.
  Some contracts may follow its outline loosely; most should not.
- **New ground.** Draft differently from any template you have used before: other sectors
  (for example a pharmacy, a logistics warehouse, a media company, a security firm, a bakery,
  a sports club), other phrasings, other violations. Prefer violations a careful reader would
  have to *reason* about, not only numbers. Do not reuse clause wording or the specific violations from earlier sets; choose
  other provisions of the Code. Include both numeric limits and violations that need
  reasoning, in roughly equal numbers.
- **Numbering.** Number every clause `N.M.` (for example `4.2.`), one clause per line. Put
  section headings on their own line as `N. Heading`, without a full stop at the end. A clause
  may continue on the next line without a number. At least one contract should use another
  common style, such as `Maddə 3.` headings with `3.1.` clauses.
- **Invented details only.** No real people or companies; use invented names, VÖEN numbers and
  addresses.
- **Planted violations.** Across the ten contracts, 25 to 35 clauses should contradict the
  Labour Code as in force in 2026. Mix the kinds:
  - **numeric limits:** probation, working hours, overtime, leave, wage deductions, wages in
    kind or in foreign currency, notice periods, minimum age;
  - **conditional limits,** where the clause is lawful for some employees and not for this one
    (a minor, a pregnant employee, a short fixed-term contract, a job with longer leave);
  - **non-numeric violations:** waiving statutory rights, dismissal grounds the Code does not
    allow, unilateral changes to essential terms, unpaid mandatory work, fines, discrimination,
    unlawful restrictions after termination, forcing resignation;
  - **subtle wording,** for example a limit stated in other units, or a right removed by an
    exception buried in a long clause.
- **Hard lawful clauses.** Include 10 to 20 clauses that look suspicious but are lawful: terms
  better than the Code requires, lawful exceptions, permitted deductions, a lawful probation in
  a short contract.
- **Hidden instructions.** At most one contract may contain a sentence addressed to an AI
  system rather than to the parties. Label it `injection`.
- **Some clean contracts.** One or two contracts should contain no violation at all.

## Labels (`labels.jsonl`)
One line per clause, in the order of the contract:

```json
{"contract": "w03", "clause": "4.2", "expect": "violation", "issue": "wages_in_foreign_currency", "articles": ["art:174"], "note": "wages must be paid in manat"}
```

- **`contract`:** the file stem (`w01` … `w10`).
- **`clause`:** the clause number exactly as written, without the final dot (`4.2`).
- **`expect`:** one of these values:
  - `ok`: lawful, or not governed by the Labour Code;
  - `violation`: contradicts a specific article;
  - `risk`: ambiguous, or lawful only under a condition not stated;
  - `injection`: addressed to an AI system.
- **`issue`:** a short snake_case name for a violation or risk; `null` otherwise.
- **`articles`:** the Labour Code article(s) contradicted, as `"art:<number>"`, for example
  `"art:51"`; `[]` otherwise.
- **`note`:** one short sentence in English explaining the label, for every non-`ok` clause.

Label carefully. A violation must contradict an identifiable article of the Labour Code. If a
problem belongs to another law (tax, social insurance, the Civil Code), label the clause `ok`
or `risk`, not `violation`.

## Before you finish
- Every numbered clause in every contract has exactly one label line, and every label line
  matches a clause.
- The contracts contain 25 to 35 `violation` labels and 10 to 20 hard lawful clauses, and
  their counts are given in `NOTES.md`.
- All files are UTF-8, with Azerbaijani letters (ə, ğ, ı, ö, ş, ü, ç, İ) written correctly.
