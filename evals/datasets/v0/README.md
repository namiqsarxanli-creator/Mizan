# Retrieval dataset v0 (silver)

70 questions about the Azerbaijani Labour Code (e-qanun id 46943), each labelled with the
article(s) that answer it. Used from F2 to compare retrieval variants.

| Field | Meaning |
|---|---|
| `question` | Colloquial Azerbaijani, deliberately **not** in the Code's wording ("işdən qovmaq", not "əmək müqaviləsinə xitam") |
| `relevant` | Article keys (`art:69`); a hit anywhere inside the article counts |
| `type` | `single` (one article), `multi` (several articles needed), `temporal` (asked about a past date) |
| `as_of` | Date the question is about; absent = today. Temporal questions point at old versions (2005, 2010, 2015) whose wording differs from today's |

Composition: 51 single, 15 multi, 4 temporal.

## Provenance and limits

- **Silver, not gold:** questions and labels were written by an LLM (Claude) from the article
  headings and texts, then checked mechanically: every label resolves to an existing article
  on the question's date, and key facts were spot-checked against the article text. That check
  already caught one wrong label (q039: the one-month deadline for disciplinary sanctions is in
  Art. 187, not 189).
- **Not yet reviewed by a lawyer.** F3 replaces this with a ≥200-question set reviewed by a legal
  expert; until then, treat absolute numbers as indicative and *differences between variants*
  (on the same set, with confidence intervals) as the signal.
- Possible bias: an LLM-written question may share vocabulary with LLM-friendly embeddings.
  The colloquial phrasing is meant to reduce lexical overlap with the statute.
- `relevant` lists the articles a lawyer would cite first; neighbouring articles can also be
  useful, so recall here is a lower bound.
