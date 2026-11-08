# Generation evaluation

Questions: 59 (failed: 1). Judge-based numbers are uncalibrated until `mizan-eval kappa` reaches the target.

| Metric | Value |
|---|---|
| Answer correct (all) | 0.914 [0.84, 0.98] (n=58) |
| Answer correct (in scope) | 0.907 [0.81, 0.98] (n=54) |
| Abstention on out-of-scope | 1.000 [1.00, 1.00] (n=4) |
| Cites a gold article | 0.889 [0.80, 0.96] (n=54) |
| Citation valid (verbatim) | 0.842768959435626 |
| p50 latency (ms) | 2594.2 |

## Answer correct by question type

| Type | Correct |
|---|---|
| exact_term | 1.000 [1.00, 1.00] (n=6) |
| false_premise | 1.000 [1.00, 1.00] (n=3) |
| multi | 0.889 [0.67, 1.00] (n=9) |
| no_answer | 1.000 [1.00, 1.00] (n=4) |
| single | 0.900 [0.80, 1.00] (n=30) |
| temporal | 0.833 [0.50, 1.00] (n=6) |

## Incorrect answers

- `q049` (single) Yetkinlik yaşına çatmayanlara hansı işlərdə işləmək qadağandır? — judge: The answer adds prohibited work (night work, overtime, heavy object lifting) not specified in the gold provisions.
- `q097` (single) Seçki günü işçilər işə çıxmalıdırmı? — judge: The answer adds exceptions not present in the gold provisions, contradicting the rule that election day is not a working day.
- `q120` (single) Kənd təsərrüfatında iş vaxtı və əmək haqqı necə tənzimlənir? — judge: The answer omitted the shareholding-based determination of wages, a condition that changes the legal conclusion.
- `q154` (multi) İşə girəndə hansı sənədləri verməliyəm və müqavilədə nələr yazılmalıdır? — judge: The answer omitted the mandatory contract date requirement.
- `q165` (temporal) Hamiləliyə və doğuşa görə məzuniyyət neçə gün idi? — judge: The answer omitted the longer maternity leave periods for agricultural workers, which changes the conclusion.

## Judge calibration (2026-11-08)
**Human.** One person, not a lawyer, labelled 10 answers blind
([`human_labels.jsonl`](human_labels.jsonl), `mizan-eval label --web`). The labeller compared
each answer with the gold provisions as shown.

- All 10 were labelled correct, and the judge agrees on all 10.
- With no incorrect answer in the sample, Cohen's κ is undefined: chance agreement is already
  1. This is consistent with the judge but does not calibrate it.

**A second model** ([`second_rater.py`](second_rater.py), [`second_rater.json`](second_rater.json)).
`gemini-2.5-flash`, from another family than the judge (`gpt-oss-20b`), labelled answers blind,
with the human's instructions. Its free daily quota ran out after **23 of 58**.

| | n | agreement | κ |
|---|---|---|---|
| judge vs Gemini | 23 | 0.70 | **−0.08** |
| Gemini vs human | 10 | 7/10 | – |
| judge vs human | 10 | 10/10 | undefined |

**What it means.**

- **Gemini is much stricter.** It failed 6 answers the judge passed, nearly always for "a
  condition that changes the conclusion is missing": extensions of an age limit, the absolute
  time limit on sanctions, the grounds that trigger severance pay.
- **At least one of its fails is wrong.** On q003 it held that redundancy might fall under the
  exceptions of art. 79.2. Redundancy is art. 70 "b"; the exceptions are "a" and "e". The human
  and the judge were right.
- **Several of its points are fair.** On q042 the answer makes an employee liable for accidental
  breakage that art. 201 excludes.
- **The judge's 0.914 is best read as a lenient score.** It credits the main conclusion and
  forgives omitted conditions. A stricter reader, model or lawyer, would score lower.
- **The judge remains uncalibrated** in the sense this phase intended. That needs more human
  labels, ideally a lawyer's, including incorrect answers.
