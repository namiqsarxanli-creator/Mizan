# Retrieval evaluation

- Dataset: `evals/datasets/v1/questions.jsonl` (200 questions: 20 exact_term, 10 false_premise, 35 multi, 101 single, 19 temporal)
- Generated: 2026-10-18 10:25 UTC
- Metrics at article level; `mean [95% bootstrap CI]`; latency is p50 on CPU.

| Variant | Hit@1 | Hit@5 | Recall@5 | MRR@10 | nDCG@10 | p50 ms |
|---|---|---|---|---|---|---|
| no graph | 0.832 [0.78, 0.88] | 0.973 [0.95, 0.99] | 0.948 [0.92, 0.97] | 0.894 [0.86, 0.93] | 0.901 [0.87, 0.93] | 161 |
| graph out seeds=1 slots=1/5 + cited-article routing | 0.843 [0.79, 0.89] | 0.973 [0.95, 0.99] | 0.950 [0.92, 0.98] | 0.901 [0.87, 0.93] | 0.907 [0.88, 0.93] | 167 |
| graph out seeds=1 slots=2/5 + cited-article routing | 0.843 [0.79, 0.89] | 0.973 [0.95, 0.99] | 0.948 [0.92, 0.97] | 0.901 [0.87, 0.93] | 0.907 [0.88, 0.93] | 167 |

## Recall@5 by question type

| Variant | exact_term | false_premise | multi | single | temporal |
|---|---|---|---|---|---|
| no graph | 0.95 | 0.95 | 0.85 | 0.98 | 0.95 |
| graph out seeds=1 slots=1/5 + cited-article routing | 1.00 | 0.95 | 0.87 | 0.97 | 0.95 |
| graph out seeds=1 slots=2/5 + cited-article routing | 1.00 | 0.95 | 0.85 | 0.97 | 0.95 |

## Misses of the best variant (graph out seeds=1 slots=1/5 + cited-article routing): Hit@5 = 0

- `q002` Müdir məni heç bir səbəb göstərmədən işdən çıxara bilərmi? → expected art:68, art:70
- `q014` Uzun illər eyni yerdə işləyənə əlavə istirahət günləri düşürmü? → expected art:116
- `q059` İşə qəbulda qadın olduğuma görə məndən imtina edə bilərlərmi? → expected art:16
- `q068` İşdən çıxmaq üçün işəgötürəni nə qədər əvvəl xəbərdar etməliyəm? → expected art:69
- `q079` Qanundakı müddətin son günü istirahət gününə düşərsə, müddət nə vaxt bitir? → expected art:8
