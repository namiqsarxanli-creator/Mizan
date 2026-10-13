# ADR-0002: Vector database — Qdrant

- **Status:** Accepted
- **Date:** 2026-10-13
- **Phase:** F0 (exercised from F2)

## Context
Retrieval is the foundation of every later phase. Requirements:

1. **Hybrid search:** dense vectors plus sparse/lexical signals. Legal queries often hinge on
   exact terms and article numbers ("77-ci maddə") that embeddings blur.
2. **Rich payload filtering:** every chunk carries `code`, `article_no`, `valid_from`,
   `valid_to`, `tenant_id`. Temporal queries ("what applied in 2020?") are range filters that must
   run *inside* the ANN search, not as a post-filter that silently drops recall.
3. **Several vector spaces per chunk:** we will compare base vs fine-tuned embeddings (F7) and
   need blue-green re-indexing without downtime.
4. Scale: on the order of 10^5–10^6 chunks. Not a scale problem, so ergonomics and correctness win.

## Options considered
| Option | Pros | Cons |
|---|---|---|
| **pgvector** | One database; transactional with metadata; simple ops | Filtered HNSW search can lose recall under selective filters; sparse/hybrid needs extra work; one index per table/column makes blue-green re-indexing clumsy |
| **Qdrant** | Filter-aware HNSW (payload indexes are used during graph traversal); native sparse vectors and named vectors per point; collection aliases for zero-downtime swaps | One more service to run and back up; data duplicated between Postgres (source of truth) and Qdrant |
| Elasticsearch / OpenSearch | Excellent BM25; kNN support | Heavy to operate; dense search is not its strength; JVM tuning |
| Managed (Pinecone etc.) | No ops | Vendor lock-in; cost; less control for experiments |

## Decision
**Qdrant** for retrieval, with **Postgres as the source of truth**. Qdrant is a derived index
that can always be rebuilt from Postgres by the ingestion pipeline.

- Named vectors: `dense_base`, `dense_ft`, `sparse` on the same point.
- Collection aliases (`legal_current` → `legal_v3`) for blue-green re-indexing.
- Payload indexes on `code`, `article_no`, `valid_from`, `valid_to`, `tenant_id`.

## Consequences
- Hybrid + temporal filtering is handled inside one query, measured in F2.
- We must keep Postgres and Qdrant in sync. The ingestion pipeline is idempotent (content hash)
  and Qdrant point IDs are derived deterministically from chunk IDs, so a re-run converges.
- Backups only need to cover Postgres; Qdrant is reproducible (at the cost of re-embedding time).
- **How we will know it was wrong:** in F2, if pgvector (benchmarked on the same eval set) matches
  Qdrant's Recall@5 within the confidence interval *with* temporal filters, the extra service is
  not paying for itself and we revisit.
