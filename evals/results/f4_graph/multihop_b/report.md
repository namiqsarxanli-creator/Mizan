# Retrieval evaluation

- Dataset: `evals/datasets/v1/multihop_b.jsonl` (30 questions: 30 multihop)
- Generated: 2026-10-18 10:26 UTC
- Metrics at article level; `mean [95% bootstrap CI]`; latency is p50 on CPU.

| Variant | Hit@1 | Hit@5 | Recall@5 | MRR@10 | nDCG@10 | p50 ms |
|---|---|---|---|---|---|---|
| no graph | 0.767 [0.60, 0.90] | 0.933 [0.83, 1.00] | 0.700 [0.60, 0.80] | 0.836 [0.72, 0.94] | 0.704 [0.61, 0.79] | 196 |
| graph out seeds=1 slots=1/5 + cited-article routing | 0.767 [0.60, 0.90] | 0.933 [0.83, 1.00] | 0.817 [0.70, 0.92] | 0.836 [0.72, 0.94] | 0.744 [0.64, 0.83] | 204 |
| graph out seeds=1 slots=2/5 + cited-article routing | 0.767 [0.60, 0.90] | 0.933 [0.83, 1.00] | 0.817 [0.70, 0.92] | 0.836 [0.72, 0.94] | 0.751 [0.65, 0.84] | 201 |

## Recall@5 by question type

| Variant | multihop |
|---|---|
| no graph | 0.70 |
| graph out seeds=1 slots=1/5 + cited-article routing | 0.82 |
| graph out seeds=1 slots=2/5 + cited-article routing | 0.82 |

## Misses of the best variant (graph out seeds=1 slots=1/5 + cited-article routing): Hit@5 = 0

- `mhb26` İşəgötürən əmək qanunvericiliyini pozaraq işçiyə ziyan vurarsa, hansı məsuliyyətə cəlb olunur? → expected art:186, art:311
- `mhb28` İşəgötürən qadın olduğuna görə işçinin maaşını kişilərdən az təyin edə bilərmi? → expected art:154, art:16
