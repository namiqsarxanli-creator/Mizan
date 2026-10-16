# ADR-0004: Retrieval configuration, chosen by measurement

- **Status:** Accepted
- **Date:** 2026-10-15
- **Phase:** F2

## Context
F2 had to pick, for Azerbaijani legal text and colloquial Azerbaijani questions:

1. a lexical analyser for BM25 (Azerbaijani is agglutinative; no maintained stemmer exists),
2. a dense embedding model (multilingual, CPU-servable),
3. chunk granularity (whole article vs. part),
4. whether to fuse lexical and dense results (hybrid / RRF),
5. whether a cross-encoder reranker and LLM query rewriting pay for their latency.

Each of these is commonly "known" to help. None of that folklore was measured on this
language or this kind of question, so every option was evaluated instead of assumed.

### Method
- **Dataset:** `evals/datasets/v0/retrieval.jsonl`, 70 questions (51 single-article,
  15 multi-article, 4 temporal) written in everyday Azerbaijani, deliberately avoiding the
  Code's wording; labels are article keys, checked against the stored text (one wrong label
  was caught and fixed). Silver quality: LLM-authored, not yet lawyer-reviewed (F3).
- **Metrics** at article level (chunks de-duplicated by article in rank order): Hit@1, Hit@5,
  Recall@5, MRR@10, nDCG@10, each with a 95% bootstrap CI (2,000 resamples of questions).
  With 70 questions one question moves Hit@5 by ~1.4 points, so differences inside
  overlapping intervals are not treated as wins.
- **Temporal correctness** is built in: every query is filtered to chunks valid on its date,
  so a temporal question retrieving today's text scores as a miss.
- **Hardware:** laptop CPU (16 cores, no GPU); latency is p50 per query.
- Full per-question results: `evals/results/f2_retrieval/` (`report.md`, `results.json`,
  one checkpoint per variant). Reproduce with `uv run mizan-retrieval eval`.

## Results (full table: `evals/results/f2_retrieval/report.md`)

All on article chunks unless noted; `mean [95% CI]`; p50 latency per query on CPU.

| Variant | Recall@5 | MRR@10 | Hit@1 | p50 |
|---|---|---|---|---|
| BM25, unstemmed | 0.550 [0.44, 0.66] | 0.404 [0.31, 0.50] | 0.286 | 272 ms |
| BM25, prefix-5 | 0.593 [0.48, 0.70] | 0.495 [0.40, 0.59] | 0.386 | 270 ms |
| BM25, prefix-6 / prefix-7 | 0.586 / 0.579 | 0.464 / 0.457 | | |
| e5-large (dense) | 0.790 [0.70, 0.88] | 0.704 [0.62, 0.79] | 0.586 | 379 ms |
| **bge-m3 (dense)** | **0.929 [0.87, 0.98]** | **0.831 [0.76, 0.90]** | 0.743 | **376 ms** |
| bge-m3, part chunks | 0.905 [0.84, 0.96] | 0.818 [0.74, 0.89] | 0.729 | 372 ms |
| bge-m3 + BM25-p5 (RRF) | 0.895 [0.83, 0.95] | 0.741 [0.67, 0.82] | 0.600 | 389 ms |
| bge-m3 + rerank@20 | 0.910 [0.85, 0.96] | 0.863 [0.80, 0.93] | 0.786 | 17,167 ms |
| bge-m3 + BM25-p5 + rerank@20 | 0.910 [0.85, 0.96] | 0.853 [0.79, 0.92] | 0.771 | 18,321 ms |
| bge-m3 + LLM rewrite | 0.914 [0.86, 0.96] | 0.813 [0.74, 0.88] | 0.700 | 398 ms + ~1.5 s LLM |

What the numbers say:

1. **Dense retrieval dominates.** bge-m3 beats e5-large clearly (non-overlapping Recall@5
   intervals) and beats every BM25 variant by ~35 points: colloquial questions share few
   words with the statute, and only a semantic model bridges "işdən qovmaq" and
   "əmək müqaviləsinə xitam".
2. **Prefix stemming helps BM25**, as the Turkish IR literature predicted: prefix-5 adds
   +4 points Recall@5 and +9 points MRR over the unstemmed analyser; 6 and 7 are in between.
3. **Hybrid fusion hurts here.** RRF with BM25 lowers MRR from 0.831 to 0.741 and multi-article
   recall from 0.80 to 0.71: the lexical ranking is weak on this question style and RRF gives
   it equal say. This is the opposite of the usual advice, and it is specific to colloquial
   queries; queries quoting statutory terms or article numbers may well favour hybrid.
4. **Reranking is not worth it on CPU.** +3 points MRR (inside the confidence interval) for a
   45x latency increase (0.4 s to 17 s per query).
5. **Query rewriting brings no significant gain** (Hit@5 0.971 vs 0.957, MRR slightly lower)
   while adding an external LLM call to every request.
6. **Article chunks are at least as good as part chunks** for article-level retrieval; parts
   remain useful for parent-child expansion of long articles before answering.

Three questions are missed by the winner (Hit@5 = 0): q002 (needs two articles on dismissal
grounds), q014 (seniority leave) and q068, a 2015 temporal question whose then-current
wording ("ləğv edilməsi") differs from today's ("xitam verilməsi").

## Decision
Serve **dense bge-m3 over article chunks, no fusion, no reranking, no query rewriting** by
default (`RetrievalSettings.mode = "dense"`, `rerank_top = 0`, `LLMSettings.rewrite_queries
= False`). Before answering, hits are expanded to whole articles (parent-child).

Everything else stays implemented, tested and switchable by configuration, because the
trade-offs depend on deployment and query mix:

- `MIZAN_RETRIEVAL__RERANK_TOP=20` on a GPU deployment, where the reranker costs ~100 ms and the
  +3 points MRR become worth having.
- `MIZAN_RETRIEVAL__MODE=hybrid` once the query mix includes practitioners' exact-term queries.
- `MIZAN_LLM__REWRITE_QUERIES=true` for experiments; the F5 planner agent supersedes it.

## Consequences
- F2 Definition of Done met: Recall@5 0.929 (CI lower bound 0.87) against a target of 0.75,
  at 376 ms p50 on a laptop CPU with no external dependency in the retrieval path.
- The API needs the bge-m3 model (~2.3 GB) in its image or model cache; the embedding cache
  makes re-indexing cheap.
- **Known limits of this evidence:** 70 silver questions, all colloquial. F3 replaces them
  with a lawyer-reviewed set of ≥200 questions including exact-term, multi-hop and temporal
  ones; every conclusion above is re-tested there, and the CI eval gate then guards it.
- **Retrieval quality is not answer quality.** In a live end-to-end check, "how many days'
  notice must I give my employer to quit?" retrieved Art. 69 (one month) at rank 5 and the
  model answered from Art. 77 (the *employer's* redundancy notice) at rank 2, with a verbatim
  and therefore "valid" quotation. Quote verification proves a citation exists, not that it
  answers the question. F3 adds answer-correctness and faithfulness metrics; F5 adds the
  verifier agent that checks each claim against the question.
- **How we will know it was wrong:** on the F3 set, hybrid or reranking beating dense-only
  outside the confidence intervals, or temporal questions (only 4 here) showing systematic
  misses like q068.
