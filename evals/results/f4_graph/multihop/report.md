# Retrieval evaluation

- Dataset: `evals/datasets/v1/multihop.jsonl` (40 questions: 40 multihop)
- Generated: 2026-10-18 10:26 UTC
- Metrics at article level; `mean [95% bootstrap CI]`; latency is p50 on CPU.

| Variant | Hit@1 | Hit@5 | Recall@5 | MRR@10 | nDCG@10 | p50 ms |
|---|---|---|---|---|---|---|
| no graph | 0.850 [0.75, 0.95] | 1.000 [1.00, 1.00] | 0.838 [0.76, 0.91] | 0.911 [0.84, 0.97] | 0.815 [0.75, 0.89] | 208 |
| graph out seeds=1 slots=1/5 + cited-article routing | 0.850 [0.75, 0.95] | 1.000 [1.00, 1.00] | 0.912 [0.85, 0.96] | 0.911 [0.84, 0.97] | 0.846 [0.78, 0.91] | 212 |
| graph out seeds=1 slots=2/5 + cited-article routing | 0.850 [0.75, 0.95] | 1.000 [1.00, 1.00] | 0.925 [0.86, 0.97] | 0.911 [0.84, 0.97] | 0.857 [0.79, 0.92] | 213 |

## Recall@5 by question type

| Variant | multihop |
|---|---|
| no graph | 0.84 |
| graph out seeds=1 slots=1/5 + cited-article routing | 0.91 |
| graph out seeds=1 slots=2/5 + cited-article routing | 0.93 |

## Misses of the best variant (graph out seeds=1 slots=2/5 + cited-article routing): Hit@5 = 0

