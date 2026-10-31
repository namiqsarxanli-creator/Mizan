# ADR-0013: Contract analysis: rules first, LLM second, every finding citing the law

- **Status:** Accepted
- **Date:** 2026-10-31
- **Phase:** F10

## Context
F10 adds the first feature for people who are not asking a question: upload an employment
contract and get back the clauses that contradict the Labour Code. Its constraints:

- **Wrong findings are costly both ways.** A false alarm makes a lawful contract look illegal.
  A miss on, say, a six-month probation is exactly what a user wanted caught.
- **Uploads are hostile by default.** A contract is text written by someone else, possibly
  addressed to the model rather than to the parties (indirect prompt injection).
- **Formats vary.** Contracts arrive as DOCX, PDFs with a text layer, scanned PDFs and phone
  photos.
- **Most violations are numbers.** Probation length, weekly hours, days of leave, the overtime
  multiplier, the share of wages paid in kind, the resignation notice. They are numbers checked
  against a limit in a named article.
- **No real contracts yet.** The DoD asks for 10 real contracts. Until they arrive, the only
  labelled data is synthetic and written by the same author as the checks.

## Options considered
| Option | Pros | Cons |
|---|---|---|
| A. LLM only: the whole contract plus retrieved articles, "find the violations" | covers everything in one prompt | misses arithmetic (48 h > 40 h); invents articles; one long prompt per contract; injection lands in the prompt |
| B. Rules only | exact, free, testable, immune to injection | covers only what someone thought to encode |
| C. Rules for the quantitative limits, LLM per batch of clauses for the rest, findings kept only with a verified quote | each layer does what it is good at | two layers to maintain |

## Decision
**C.** The pipeline is `mizan_contracts.analysis`:

1. **Read.**
   - PDF text layers are read with `pypdf`.
   - Pages without text (scans) are rendered with `pypdfium2` and read by a vision model on
     Groq.
   - Photos go straight to the same model.
   - Without a key, scanned pages are reported as unreadable, never guessed.
2. **Split.** The text is split into numbered clauses under section headings. Unnumbered text
   falls back to paragraphs.
3. **Screen.** Every clause passes the F10 injection screen (`channel="document"`).
   - A flagged clause is reported as `injection` and never sent to a model.
   - The request is audited with outcome `injection`.
   - The upload is not refused. The person uploading is usually the target of the hidden text,
     not its author, and the report should tell them.
4. **Rules** (`rules.py`). Twelve limits from articles 51, 52, 69, 70, 77, 79, 89, 91, 100, 114,
   165, 174, 175 and 176.
   - Contract-level facts change the limit: a fixed term (art. 51: two weeks of probation in a
     contract of up to six months), a minor (art. 52, 91), or a job with 30 days of leave
     (art. 114.3).
   - Each finding names its article and explains itself in Azerbaijani.
5. **LLM review** (`review.py`). Covers the clauses no rule flagged, in batches of up to six
   from the same section.
   - For each clause, provisions are retrieved and interleaved so every clause keeps its best
     source.
   - The model returns `ok`, `risk` or `violation` per clause, with a source number and a
     quote.
   - **A risk or violation is kept only if its quote occurs verbatim in the cited source.**
     This is the `/v1/ask` citation check. The article key comes from that source, not from the
     model.
   - Unverifiable findings are dropped and counted.
6. **Verdict per clause:** `injection` > `violation` > `risk` > `ok`.
7. **Report.** The report and the page carry the disclaimer ("not legal advice", "OK means no
   contradiction was found, not that the clause is lawful").

**Serving.**

- `POST /v1/contracts/analyze`: multipart, 10 MB, 40 pages, scope `ask`. LLM tokens are charged
  to the tenant's daily budget.
- `GET /contracts`: one page that renders answers with `textContent` only (OWASP LLM05).

## Evaluation
**Development set.**

- 12 synthetic contracts on the Code's model form (annex 1), each for a different sector.
- 256 clauses, 20 planted violations and one injected instruction.
- It includes hard lawful clauses: a two-week probation in a five-month contract, overtime at
  2.5x, 30 days of leave for a laborant.

**Rules on it.** 20/20 found, 0 false positives, the injection caught.

- They were written on this set, so this is a regression gate (CI: `mizan-eval contracts
  --gate`), not evidence that they generalise.
- The LLM layer is measured alone on every clause; see
  [`evals/results/f10_contracts`](../../evals/results/f10_contracts/README.md).

**Test set.** Real contracts, analysed once with the pipeline frozen in a commit before they
are read. Until they exist, F10's DoD item stays open.

## Consequences
- **Fixed cost per rule.** Adding a check is a function and a test row. The rules are the
  cheapest, most reliable part, and each one says which article it enforces.
- **Lower LLM cost.** Clauses a rule flagged are not sent to the model. A typical contract
  costs about four LLM calls.
- **Known gaps:**
  - The rules read Azerbaijani phrasing and numbers. An unusual wording ("yarım il") falls
    through to the LLM layer.
  - Only the Labour Code is in the corpus. Clauses governed by other laws (taxes, social
    insurance, non-compete under the Civil Code) can only be marked `ok` or `risk`.
  - OCR tokens are not yet charged to the tenant's budget; the page cap and the rate limit
    bound them.
- **One author.** The synthetic set shares its author with the rules. The real-contract test is
  what turns "works on what it was built for" into a measured claim.
