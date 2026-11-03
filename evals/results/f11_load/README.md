# F11 load test: 10, 50 and 100 concurrent users

**Setup.**

- **Harness:** [`infra/loadtest`](../../../infra/loadtest/) (Locust); data in
  [`report.json`](report.json) and the `u*_stats*.csv` files.
- **Server:** one uvicorn worker, as in the Docker image, with the compose stack behind it.
- **Machine:** Windows 11, 16 logical CPUs, no GPU.
- **Real components:**
  - bge-m3 query embedding on CPU;
  - Qdrant search and article expansion;
  - the injection screen;
  - Redis (budget, audit queue);
  - the Postgres quality log.
- **LLM:** a stub that waits 1.6 s ± 25%, about the real answerer's median, and cites the top
  source. Groq's free tier (8k tokens a minute) cannot carry 100 users, so a real provider
  would have measured its rate limit, not Mizan.
- **Off for the test:** the per-subject rate limit and online evaluation.

**Workload.** Each user waits 1–3 s between requests:

| share | request |
|---|---|
| 50% | `GET /v1/search` |
| 30% | `POST /v1/ask` |
| 10% | contract analysis, rules only |
| 10% | `GET /health/ready` |

Questions are the 200 v1 eval questions. Each level ran 120 s after a 20 s warm-up.

## Results
**No request failed: 3,163 requests, 0 errors.**

| users | endpoint | req/s | p50 | p95 | p99 |
|---|---|---|---|---|---|
| 10 | search | 2.0 | 170 ms | 460 ms | 810 ms |
| 10 | ask (stub LLM 1.6 s) | 1.1 | 1.8 s | 2.2 s | 2.5 s |
| 10 | contracts (rules) | 0.4 | 14 ms | 28 ms | 39 ms |
| 10 | ready | 0.4 | 10 ms | 26 ms | 70 ms |
| 50 | search | 5.8 | 2.0 s | 4.3 s | 4.8 s |
| 50 | ask | 3.3 | 3.8 s | 6.0 s | 6.9 s |
| 50 | contracts (rules) | 1.2 | 19 ms | 56 ms | 120 ms |
| 50 | ready | 1.2 | 25 ms | 110 ms | 170 ms |
| 100 | search | 5.7 | 6.3 s | 13 s | 14 s |
| 100 | ask | 3.4 | 8.0 s | 14 s | 15 s |
| 100 | contracts (rules) | 1.2 | 22 ms | 84 ms | 160 ms |
| 100 | ready | 1.1 | 24 ms | 150 ms | 240 ms |

Total throughput: 3.9 req/s with 10 users, 11.4 with 50, and 11.4 with 100.

## The bottleneck: query embedding on CPU
- **Throughput stops growing between 50 and 100 users.** The added users only queue.
- **Requests that embed a query slow down; the rest do not.** Search and ask together
  saturate at about 9 req/s. Contract analysis and readiness, which embed nothing, stay under
  250 ms at p99 with 100 users. So the event loop is not blocked (the embedding already runs
  in a thread), and Postgres, Redis and Qdrant are not the limit.
- **Measured directly** on the same machine, one bge-m3 query embedding takes **88 ms (11.4
  per second)**, which is the ceiling seen above. Batched, the model does more:

| batch | time | queries/s |
|---|---|---|
| 1 | 88 ms | 11.4 |
| 8 | 410 ms | 19.5 |
| 32 | 1.38 s | 23.2 |

**Capacity.**

- A user who thinks for 1–3 s and embeds on 80% of requests needs about 0.4 embeddings a
  second.
- One API process therefore serves **about 25 concurrent users** before latency climbs,
  which matches the 10-user level being comfortable and 50 saturated.
- With a real LLM, the provider's rate limit binds long before this: the free Groq tier
  allows a few `/v1/ask` per minute.

## What would raise the ceiling, in order of cost
1. **Micro-batching query embeddings** (done; see the follow-up below). Collect requests for a few ms and embed them
   together: about 2x throughput on the same CPU, per the table above. It is a small change
   to `Searcher`, and the cheapest win.
2. **A cache of query embeddings.** It already exists (`EmbeddingCache`) and helps only
   repeated questions; real traffic repeats far less than this test did.
3. **ONNX / int8 for the query encoder.** F7 exported the reranker the same way. It is
   typically 2–3x on CPU and needs a retrieval-quality check against the gate.
4. **More processes or a separate embedding service.** Each uvicorn worker loads its own
   bge-m3 (about 2 GB), so a shared embedding service (TEI or similar) scales better than
   more workers.
5. **A GPU.** One small GPU embeds thousands of queries a second, and the bottleneck moves to
   the LLM provider.

Nothing here changes a quality metric. The test measures serving cost only.

## Follow-up: micro-batching query embeddings (A/B)
`QueryBatcher` (`mizan_retrieval.embeddings`) embeds concurrent queries together, waiting at
most 10 ms for a batch.

- **It changes nothing in retrieval.** Over 48 questions, cosine with the single-query
  embedding was 1.000000 and the top-5 was identical.
- **The comparison.** Both configurations ran back to back on an otherwise idle machine, with
  the same harness ([`ab_unbatched`](ab_unbatched/), [`ab_batched`](ab_batched/)):

| users | unbatched req/s | batched req/s | search p50 unbatched → batched | ask p95 unbatched → batched |
|---|---|---|---|---|
| 10 | 3.7 | 3.8 | 180 → 230 ms | 2.3 → 2.3 s |
| 50 | 7.1 | **10.4** | 4.6 → 2.3 s | 11.0 → 6.8 s |
| 100 | 5.5 | **10.7** | 14 → 7.0 s | 35 → 16 s |

**Under load, batching keeps throughput from collapsing.** Unbatched, concurrent encodes fight
for the same cores and throughput *falls* as users are added. Batched, it holds at about
10.5 req/s. At low load it costs about 50 ms of median search latency: the wait for
companions, plus a batch that is sometimes larger than one.

**The machine itself varies a lot.** The unbatched configuration reached 11.4 req/s in the
first run above and 7.1 in this one. Only the paired comparison is meaningful; repeat it on
dedicated hardware before quoting absolute numbers.

**Decision.** Batching is on by default (`MIZAN_RETRIEVAL__BATCH_QUERIES`). The capacity
estimate stays at about 25 concurrent users per process. Batching stops the collapse beyond
that point; it does not double the ceiling.

A third run, made while other heavy jobs shared the machine, was discarded for that reason.
