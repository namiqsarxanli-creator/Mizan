# Label check (PROTOCOL.md, amendments 1 and 3)

- **Date:** 2026-11-01, before the system was run on this set.
- **Checked:** all 28 `violation` labels, the 2 `risk` labels and the 15 hard lawful clauses
  that `NOTES.md` lists. Each was read against the article texts in the corpus.
- **Not looked at:** any output of the system.
- **Clause splitting:** `split_clauses` yields exactly the labelled clause numbers in all ten
  contracts (228/228).

**About the author's isolation.** The brief told the author not to read the first test set.
This set was written in the same Codex workspace that produced the first one, so the author
had already seen it, having written it. What the instruction could still secure was new
contracts, not copies. This set shares no clause wording with the first and covers different
violations.

## Changes
| clause | was | now | reason |
|---|---|---|---|
| u05 2.1 | art. 253 | art. 252 | Art. 253 governs minors' pay. Art. 252 is the rule the note describes: minors are examined before hiring and every year at the employer's expense. The verdict (`violation`) is unchanged. |

## Considered and left unchanged
| clause | label | note |
|---|---|---|
| u02 2.3 | violation, art. 103 | Art. 103.4 requires 12 hours between working days, but under a shift regime the rest is set by the shift schedule. The contract's shift setting makes this arguable; the author's label stands. |
| u02 3.3 | violation, art. 164 | Art. 164 lets monthly-salaried staff working within the monthly norm be paid a single extra daily rate. Whether this applies depends on facts the clause does not give; the label stands. |
| u04 3.3 | violation, art. 172 | The corpus excerpt of art. 172 shows only payment frequency; the compensation for late wages was not located in the excerpt. The label stands. |
| u03 3.1 | violation, art. 187 | The excerpt of art. 187 is partial; "one sanction per offence" was not located in it. The label stands. |
| u03 4.2, u07 2.4 | risk | Kept as the author marked them; not counted as violations. |

## Verified against the article text
- art. 52: no probation for a woman with a child under 3.
- art. 53: three days' written notice to end a probation.
- art. 59: a transfer needs consent.
- art. 66: no attestation for minors.
- art. 76: one ground for termination only.
- art. 79: no dismissal during temporary incapacity.
- art. 80: union consent for a member's redundancy.
- art. 94.3: part-time work on request for a woman with a child under 14.
- art. 96: summarised accounting allows shifts of at most 12 hours.
- art. 116: 6 extra days after 15 years of service.
- art. 129: unpaid leave only on request or by agreement.
- art. 131.4: minors may take first-year leave early.
- art. 137: recall from leave only with consent.
- art. 154.3: equal pay regardless of sex.
- art. 169: idle time paid at two thirds at least.
- art. 201: no liability for normal production risk.
- art. 46 and art. 249: no employment under 15.
- Also confirmed: art. 174 (at most 20% in kind), art. 77 (1.7x severance for 5–10 years),
  art. 69.3 (resignation on the date requested on admission to education) and art. 162 (pay
  for temporary replacement).
