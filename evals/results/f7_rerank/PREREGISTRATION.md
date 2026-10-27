# F7 reranker distillation — pre-registration

Written and committed **before** the teacher scored anything and before the student was trained.

## Systems

All rerankers rescore the **same** candidate list per question: bge-m3 dense top-20 distinct
articles (article strategy, `as_of` from the dataset). The list comes from `mizan-train
candidates` and is stored in `data/train/rerank_candidates.jsonl`. Gold is in the top 20 for
96.5% of all questions.

| name | scorer |
|---|---|
| `dense` | none: the retriever's order (current serving) |
| `teacher` | BAAI/bge-reranker-v2-m3 (568M) |
| `student` | cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 (118M), distilled from the teacher |
| `student-int8` | the student exported to ONNX, dynamic int8 quantisation, CPU |

## Training (student)

- **Data:** synthetic train questions only (603). The student sees teacher scores and never gold.
- **Loss:** listwise KL over each question's 20 candidates, temperature 1.
- **Selection:** epoch with the best validation MRR@10 (111 synthetic val questions, gold
  labels). At most two learning rates are tried (3e-5, then 1e-5 only if 3e-5 is worse than
  epoch 0); the run with the higher val MRR@10 is kept.

## Primary outcome (the DoD)

- **Eval questions:** the 336 human-written eval questions with gold labels, pooled:
  v0/retrieval 70, v1/questions 185, v1/multihop 40, v1/multihop_b 30, v1/court_decisions 11.
- **Metric:** MRR@10 against gold.
- **Retention** = MRR@10(student) / MRR@10(teacher).
- **Pass** if retention ≥ 0.95 for `student`. The same threshold is reported for `student-int8`;
  it decides whether int8 is the serving format.

## Secondary (reported, not gating)

- Per-dataset MRR@10, Hit@1 and Recall@5 for all four systems.
- Paired bootstrap 95% CI of student − dense and teacher − dense on pooled MRR@10.
- p50 / p95 CPU latency per query (20 pairs) on this laptop, for teacher, student and
  student-int8.

## Known risk, stated in advance

If the teacher itself does not beat `dense` on these questions, retention measures agreement
with a scorer that does not help. That outcome will be reported as such; it does not count as
a pass for serving purposes, whatever the ratio.
