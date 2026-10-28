# F7: reranker distillation (bge-reranker-v2-m3 → MiniLM-L12)

**Verdict: the gate failed. The student keeps 93% of the teacher's MRR@10 (target ≥ 95%) and is
significantly worse than not reranking at all. Serving stays on dense bge-m3 with no reranker
(ADR-0009).**

The protocol is in [PREREGISTRATION.md](PREREGISTRATION.md), committed before the teacher scored
anything. Full numbers are in `report.json`, produced by `mizan-train rerank-report`.

## Setup

- **Candidates:**
  - bge-m3 dense top-20 distinct articles per question, with the article version in force on the
    question's date.
  - Every reranker rescores the same list, so the only difference between systems is the scorer.
  - Gold is in the top 20 for 96.5% of questions: the ceiling for any reranker.
- **Teacher:** `BAAI/bge-reranker-v2-m3` (568M), in fp16 on the GPU, over 22k pairs.
- **Student:** `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (118M, multilingual, MS MARCO).
  - Trained with listwise KL to the teacher's softmax over each question's 20 candidates.
  - Training data: 603 synthetic training questions. The student sees no gold labels.
  - lr 3e-5, 3 epochs; val MRR@10 rose from 0.684 to 0.743 (best at epoch 3). Under the
    pre-registered rule, lr 1e-5 was not run, because 3e-5 beat epoch 0.
  - Hardware: RunPod RTX 3090, about 27 minutes in total, about $0.10.
- **int8:** ONNX export, then `onnxruntime` dynamic int8 quantisation on the CPU.

## Results (336 human-written eval questions, pooled)

| system | Hit@1 | Recall@5 | MRR@10 | MRR@10 vs dense [95% CI] | CPU p50 per question |
|---|---|---|---|---|---|
| dense (serving) | 0.807 | 0.910 | 0.876 | – | – |
| teacher | 0.827 | 0.919 | **0.900** | +0.023 [−0.001, 0.049] | 31 s |
| student (fp32) | 0.747 | 0.880 | 0.837 | **−0.040 [−0.070, −0.009]** | 3.2 s |
| student (ONNX int8) | 0.759 | 0.874 | 0.841 | −0.036 [−0.066, −0.004] | 4.0 s |

Retention (MRR@10 of the student ÷ teacher): **0.930** (int8: 0.935). Gate: ≥ 0.95, so it
**fails**.

Per set (MRR@10):

| set | n | dense | teacher | student | int8 |
|---|---|---|---|---|---|
| v1/questions | 185 | 0.894 | 0.913 | 0.854 | 0.850 |
| v0/retrieval | 70 | 0.831 | 0.872 | 0.784 | 0.791 |
| v1/multihop | 40 | 0.911 | 0.922 | 0.852 | 0.869 |
| v1/multihop_b | 30 | 0.836 | 0.867 | 0.858 | 0.892 |
| v1/court_decisions | 11 | 0.848 | 0.864 | 0.758 | 0.768 |

## Reading

1. **The risk stated in advance came true.** The teacher does not significantly beat dense
   retrieval: the interval includes 0. On these questions bge-m3 already ranks well (Recall@5
   0.91, MRR@10 0.876), which leaves a reranker little room. A student that copies an
   unhelpful teacher imperfectly can only lose, and it does.
2. **Synthetic-to-real gap, again.** The student learned the teacher's ranking on synthetic
   questions (val MRR@10 0.743) but transfers poorly to people's questions. This is the same
   failure as the F7 embedding run.
3. **int8 did not pay off on this CPU.** It was slower than PyTorch fp32 even after
   padding-aware batching and limiting threads to physical cores. With all 16 logical threads
   it was about 3x slower again.
4. **int8 scores depend on the batch.** Dynamic quantisation computes activation scales per
   batch, padding included. Reordering the batches moved pooled MRR@10 from 0.825 to 0.841.
   Serving int8 would need a fixed batching policy for reproducible rankings.

## What would change the decision

- A significant teacher gain on a larger or harder eval set. The teacher is the upper bound of
  any student.
- Training questions in the style of real users: colloquial, describing a situation rather than
  the rule, with spelling errors. These come from the F6 production feedback loop, not from an
  LLM rewriting articles.

## Reproduce

```bash
mizan-train candidates                    # needs Qdrant (bge-m3 index)
python infra/gpu/make_bundle.py bundle    # copy bundle/ to a GPU box, then there:
python pod_distill.py 3e-5                # teacher scores, student, scores of every row
# back here, with all_scores.jsonl, scored_candidates.jsonl and student/ in data/train/rerank/:
mizan-train rerank-report
```
