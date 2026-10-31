# Label check (PROTOCOL.md, amendment 1)

- **Date:** 2026-10-31, before the system was run on this set.
- **Checked:** all 29 `violation` labels, the `risk` label and the 13 hard lawful clauses that
  `NOTES.md` lists. Each was read against the article texts in the corpus (versions in force
  in 2026).
- **Not looked at:** any output of the system.
- **Clause splitting:** `split_clauses` yields exactly the labelled clause numbers in all ten
  contracts (200/200).

## Changes
None. No label is a clear legal error.

## Considered and left unchanged
| clause | label | note |
|---|---|---|
| t06 2.4 | violation, art. 16 | Art. 79.1 also names dismissal "for membership of a trade union" explicitly. Art. 16 (discrimination, including on union membership) is not wrong, so the label stands; the right-article metric may undercount if the system cites only art. 79. |
| t05 2.5 | violation, art. 69 | No article names a "forced undated resignation" directly; art. 69 (resignation is the employee's initiative) is a reasonable anchor. |
| t04 2.5 | violation, art. 125 | The entitlement removed is the annual leave, so art. 114 would also fit. Art. 125 makes maternity leave a separate entitlement; it stands. |
| t01 2.4, t06 2.3 | violation, art. 9 | The waivers of rights are void. The article excerpts in the corpus are partial, so the exact point of art. 9 was not confirmed; the label stands. |
| t03 2.3 | risk, art. 219 | Kept as `risk`, as the author marked it. It is not counted as a violation. |

## Verified against the article text
- art. 51: at most 3 months; at most 2 weeks in contracts of up to 6 months.
- art. 52: no probation for minors or for contracts of up to 2 months.
- art. 89: 8 hours a day and 40 a week.
- art. 91: 36 hours a week at ages 16–17.
- art. 100: at most 4 hours of overtime in 2 consecutive days.
- art. 114: at least 21 days of basic leave.
- art. 118: 56 days for teachers.
- art. 119: 35 days at ages 16–17.
- art. 135 and art. 144.2: leave cannot be withheld; unused basic leave is compensated on any
  termination.
- art. 174: wages are paid in manat.
- art. 176: deductions of at most 20% per payment.
- art. 186: a fine only if a collective agreement provides for it, and at most ¼ of the
  monthly wage.
- art. 242: night work and overtime for pregnant women only with written consent.
- art. 254: no night work (20:00–07:00) for minors.
- art. 79: no dismissal of pregnant women on the grounds of art. 70.
- art. 56: one month's written notice before changing working conditions.
