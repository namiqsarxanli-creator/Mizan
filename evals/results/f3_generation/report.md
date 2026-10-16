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
