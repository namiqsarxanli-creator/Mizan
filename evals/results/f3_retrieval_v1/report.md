# Retrieval evaluation

- Dataset: `evals/datasets/v1/questions.jsonl` (200 questions: 20 exact_term, 10 false_premise, 35 multi, 101 single, 19 temporal)
- Generated: 2026-10-16 19:07 UTC
- Metrics at article level; `mean [95% bootstrap CI]`; latency is p50 on CPU.

| Variant | Hit@1 | Hit@5 | Recall@5 | MRR@10 | nDCG@10 | p50 ms |
|---|---|---|---|---|---|---|
| bm25_p5 (article chunks) | 0.530 [0.46, 0.61] | 0.757 [0.69, 0.82] | 0.722 [0.66, 0.78] | 0.632 [0.57, 0.69] | 0.665 [0.61, 0.72] | 6 |
| bge-m3 (article chunks) | 0.832 [0.78, 0.88] | 0.973 [0.95, 0.99] | 0.946 [0.92, 0.97] | 0.894 [0.86, 0.93] | 0.899 [0.87, 0.93] | 167 |
| bge-m3 + bm25_p5 (article chunks) | 0.719 [0.66, 0.78] | 0.973 [0.95, 0.99] | 0.941 [0.91, 0.97] | 0.829 [0.79, 0.87] | 0.851 [0.82, 0.88] | 166 |

## Recall@5 by question type

| Variant | exact_term | false_premise | multi | single | temporal |
|---|---|---|---|---|---|
| bm25_p5 (article chunks) | 1.00 | 0.50 | 0.53 | 0.73 | 0.84 |
| bge-m3 (article chunks) | 0.95 | 0.95 | 0.84 | 0.98 | 0.95 |
| bge-m3 + bm25_p5 (article chunks) | 1.00 | 0.90 | 0.80 | 0.98 | 0.95 |

## Misses of the best variant (bge-m3 (article chunks)): Hit@5 = 0

- `q002` Müdir məni heç bir səbəb göstərmədən işdən çıxara bilərmi? → expected art:68, art:70
- `q014` Uzun illər eyni yerdə işləyənə əlavə istirahət günləri düşürmü? → expected art:116
- `q068` İşdən çıxmaq üçün işəgötürəni nə qədər əvvəl xəbərdar etməliyəm? → expected art:69
- `q079` Qanundakı müddətin son günü istirahət gününə düşərsə, müddət nə vaxt bitir? → expected art:8
- `q121` Əmək Məcəlləsinin 69-cu maddəsi nə barədədir? → expected art:69
