# ADR-0009: Model serving: public bge-m3 dense retrieval, no reranker, gated local artefacts

- **Status:** Accepted
- **Date:** 2026-10-27
- **Phase:** F7

## Context
F7 trained two models to replace public ones in the retrieval path. Both were evaluated against
gates written before training, on the human-written eval sets (336 questions with gold
articles), and both failed:

| Candidate | Gate | Result |
|---|---|---|
| `bge-m3-mizan`: bge-m3 fine-tuned on 603 synthetic questions | Recall@5 +8% relative on v1 | +0.4%; MRR@10 −0.042 (significant) |
| Distilled reranker: MiniLM-L12, 118M, student of bge-reranker-v2-m3 | MRR@10 ≥ 95% of the teacher | 93% (int8: 93.5%); −0.040 vs dense (significant) |

The serving machine is a laptop-class CPU: 8 cores and 16 GB of RAM, shared with Postgres,
Qdrant and Neo4j. The F7 measurements on this CPU, rescoring 20 candidates per question:

| Scorer | MRR@10 (pooled) | vs dense | p50 latency |
|---|---|---|---|
| bge-m3 dense, no rerank (serving) | 0.876 | – | ~0.2 s (whole search) |
| bge-reranker-v2-m3 (teacher) | 0.900 | +0.023 [−0.001, 0.049] | 19–31 s |
| student, PyTorch fp32 | 0.837 | −0.040 [−0.07, −0.01] | 1.8–3.2 s |
| student, ONNX dynamic int8 | 0.841 | −0.036 [−0.066, −0.004] | 2.5–4.0 s |

Latency ranges span two runs on different days, because the machine's load differs. The order of
the scorers was the same in both runs.

## Options considered
| Option | Pros | Cons |
|---|---|---|
| A. Dense bge-m3 only (current) | best measured quality per second; nothing to host | leaves the teacher's small, non-significant gain on the table |
| B. Teacher reranker | +0.023 MRR@10 (CI touches 0) | 19–31 s per question on CPU: unusable interactively |
| C. Distilled student (fp32 or int8) | 10x faster than the teacher | significantly *worse* than not reranking |
| D. Fine-tuned bge-m3 | in-domain synthetic gains | worse ranking on human questions |
| E. GPU inference service for the teacher | teacher quality at ~0.2 s | an always-on GPU for a gain we cannot show is real |

## Decision
Serve **option A**: public `BAAI/bge-m3` dense retrieval with the citation graph (ADR-0006) and
no reranker.

Trained models stay **local, gated artefacts**:

- They are registered only when their weights exist (`MIZAN_FINETUNED_EMBEDDINGS`).
- They are indexed side by side in the same blue-green collection (one named vector per model).
- They are compared with `mizan-train compare` / `mizan-train rerank-report`.
- A model reaches serving only by passing a gate written before it was trained. The serving
  config change is then a one-line `dense_model` / `rerank_top` switch, reversible by the alias
  swap of ADR-0002.

ONNX int8 is not adopted. On this CPU it was slower than PyTorch fp32 even after padding-aware
batching and thread tuning. Quality was on par: 0.841 vs 0.837 MRR@10.

Its scores also depend on batch composition. Dynamic quantisation derives activation scales per
batch, padding included, and reordering the batches moved pooled MRR@10 from 0.825 to 0.841.
That non-determinism is a further reason not to serve it without a fixed batching policy.
Dynamic int8 pays off on CPUs with VNNI; this one does not appear to benefit.

## Consequences
- **Easier:** no model hosting, no extra latency, nothing to keep in sync with the index.
- **Easier:** the evaluation tools stay in the repository, and the next attempt costs a GPU
  hour (about $0.25 for both F7 runs) rather than a redesign.
- **Harder, accepted:** gains that need domain adaptation wait for better training data. Both
  failures share a cause: synthetic questions are cleaner than real ones, so models trained on
  them learn the generator's style. A larger human-style question set is the prerequisite.
- **Harder, accepted:** the teacher's possible +0.02 MRR@10 is not served. Its interval includes
  zero on 336 questions, so we cannot claim it is real.

**How we will know it was wrong:**
- A teacher run on a larger or harder eval set shows a significant gain (lower bound > 0) and
  a GPU becomes available at serving time. Revisit option E.
- Production feedback (ADR-0008 quality loop) shows retrieval misses that a reranker fixes
  offline.
