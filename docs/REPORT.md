# Mizan: a measured, citation-grounded legal research system for Azerbaijani labour law

*Technical report, November 2026.* Every number below links to the result directory it comes
from. The architecture decisions are in [`docs/adr`](adr/).

## Abstract
Mizan answers questions about the Labour Code of the Republic of Azerbaijan. Each answer
cites the article in force on a given date and quotes it verbatim, so every quote is checked
mechanically against the source.

The system was built in twelve phases. Each phase had a definition of done set before its
results were known. Several evaluations were pre-registered: the protocol was committed
before the data was scored.

**Results:**

- **Retrieval:** dense bge-m3 retrieval over article-level chunks reaches Recall@5 0.946 on
  200 human questions.
- **Answer correctness:** a single-shot RAG answerer is correct on 0.91 of 58 questions.
- **Constitutional Court decisions:** with the Court's decisions in context, correctness on
  the questions they govern rises from 0.27 to 0.91.
- **Citation graph:** a temporal citation graph adds +0.075 and +0.117 Recall@5 on two
  multi-hop sets, with pre-registered tests.
- **Small local verifier:** a 2B-parameter model fine-tuned with LoRA keeps 98.4% of an API
  verifier's macro-F1 at 1/7 of the cost.
- **Prompt-injection screen:** it blocks 98.2% of held-out attacks and none of 281 real
  questions.

**Negative results**, reported as such:

- The multi-agent system was *less* correct than the single-shot baseline (−0.118,
  significant).
- Fine-tuning the embedder and distilling the reranker both missed their gates.
- The semantic cache missed its savings gate by 1.1 points.
- Contract analysis failed its definition of done on three successive held-out sets written
  by an independent author: recall 0.28 → 0.50 → 0.61 against a 0.70 threshold.

One process serves about 25 concurrent users on CPU. The limit is query embedding.

## 1. Problem and constraints
- **Labour law is temporal.** The Code has 14 versions since 1999, and the answer to "how many
  days of leave?" depends on the date asked about.
- **Users are not lawyers, and wrong answers are costly.** Every claim must therefore point
  at the provision and the version it rests on.
- **Practical constraints:**
  - one developer;
  - free or near-free compute (Groq's free tier, free Colab GPUs, about $3 of RunPod);
  - a low-resource language: an Azerbaijani word costs 3–4 tokens in small open models
    ([F8 tokenizers](../evals/results/f8_tokenizers/README.md));
  - no lawyer to label data.

## 2. System
```
e-qanun.az ──▶ ingestion (versioned, content-addressed, Postgres) ──▶ chunks with validity intervals
                                                                        │
                       ┌────────────────────────────────────────────────┘
                       ▼
 client ──▶ FastAPI ──▶ injection screen ──▶ retrieval: bge-m3 dense (Qdrant, as_of filter)
   │          │                               + citation graph (Neo4j) + Court decisions
   │          │                                              │
   │          ├──▶ /v1/ask: single-shot answerer (gpt-oss-120b) ──▶ quote verification
   │          ├──▶ /v1/agent: planner → researchers → drafter → verifier (Temporal, optional)
   │          └──▶ /v1/contracts: clauses → rules + LLM review → verified findings
   │
   └── JWT auth, rate limit, tenant budget, audit log, Postgres RLS; OpenTelemetry traces,
       quality log, online evaluation, human review queue
```

| component | choice | why (ADR) |
|---|---|---|
| storage | versioned snapshots; validity intervals derived; text content-addressed | [0003](adr/0003-temporal-versioning.md) |
| retrieval | dense bge-m3 on article chunks; hybrid, reranking and query rewriting measured and left off | [0004](adr/0004-retrieval-configuration.md) |
| graph | citations extracted by grammar into Neo4j; one reserved top-5 slot for a cited article | [0006](adr/0006-knowledge-graph.md) |
| agents | own typed state machine; the LLM only plans, drafts and verifies | [0007](adr/0007-agent-orchestration.md) |
| observability | OTel GenAI spans; answers, feedback and online evals in Postgres | [0008](adr/0008-observability.md) |
| durability | agent runs as Temporal workflows, idempotent by request id | [0011](adr/0011-durable-agent-runs.md) |
| tenancy | JWT with scopes, tenant from the token only, row-level security, audit log | [0012](adr/0012-tenancy-and-access.md) |
| contracts | LLM review with verified quotes; rules as guarded hints | [0013](adr/0013-contract-analysis.md) |

**Grounding by construction.** The answerer returns JSON with `{source, quote}` pairs. A
citation survives only if its quote occurs verbatim in the source it names. A quote elided
with "…" passes only if every segment does. The displayed quote is the statute's own text,
so a model can neither cite a provision it was not given nor put words in its mouth.

## 3. How things were measured
1. **Datasets are versioned.**
   - v0 has 70 questions; v1 has 200 human questions plus paraphrases.
   - There are multi-hop sets, a red-team set and contract sets.
   - Each split was fixed by a hash before use.
2. **Mechanical metrics come first:** Recall@k, MRR, verbatim-citation rate and correctness
   against gold provisions.
3. **An LLM judge is used only where needed,** from another model family. Calibration against
   human labels (Cohen's κ) awaits labelling; judged numbers are marked uncalibrated.
4. **Pre-registration.** When a decision rested on one comparison, the protocol, the metric
   and the threshold were committed before the test split was scored. Later deviations are
   documented in place (F4, F5, F8, F9, F10).
5. **Uncertainty:** bootstrap 95% intervals and paired tests on shared questions.
6. **Development and test are kept apart.** A set that was looked at while fixing the system
   becomes development data. The F10 contract runs follow this rule strictly.

## 4. Results
### 4.1 Retrieval ([F2](../evals/results/f2_retrieval/report.md), [v1](../evals/results/f3_retrieval_v1/report.md))
| variant (article chunks, v1, 200 questions) | Hit@1 | Recall@5 | MRR@10 | p50 |
|---|---|---|---|---|
| BM25 (Azerbaijani analyser) | 0.530 | 0.722 | 0.632 | 6 ms |
| **bge-m3 dense** | **0.832** | **0.946** | **0.894** | 167 ms |
| bge-m3 + BM25 (RRF) | 0.719 | 0.941 | 0.829 | 166 ms |

- **Hybrid search (RRF) hurt ranking.** Hit@1 fell from 0.832 to 0.719.
- **Reranking (bge-reranker-v2-m3)** added about +0.03 MRR on v0 at about 45x the latency on
  CPU.
- **LLM query rewriting** lowered MRR.
- All three are measured and off (ADR-0004).
- **The remaining misses** are colloquial questions ("Müdir məni səbəbsiz işdən çıxara
  bilərmi?") and questions about the Code itself ("what is article 69 about?").

### 4.2 Answers ([F3](../evals/results/f3_generation/report.md))
| metric (58 questions) | value |
|---|---|
| answer correct | 0.914 [0.84, 0.98] |
| abstains when out of scope | 4/4 |
| cites a gold article | 0.889 |
| citations verbatim | 0.84 |
| p50 latency | 2.6 s |

Errors are omissions of conditions, not inventions. Examples are the longer maternity leave
for agricultural workers, and exceptions added on election day.

### 4.3 Graph and court decisions ([F4 graph](../evals/results/f4_graph/), [decisions](../evals/results/f4_decisions/README.md))
- **Citation graph.** The pre-registered policy (one top-5 slot for an article cited by the
  top hit) changes nothing on ordinary questions: Recall@5 0.948 → 0.950. On multi-hop
  questions it helps:
  - +0.075 [0.03, 0.14] Recall@5 on the first set;
  - +0.117 [0.05, 0.20] on a fresh second set built to test the selected policy.
- **Constitutional Court decisions.** Attached to the provisions they interpret, they raise
  correctness on the 11 questions they govern from 0.27 to 0.91 (+0.64 [0.36, 0.91]), with 7
  questions fixed and 0 broken. The temporal control holds: a question dated before a decision
  does not receive it.

### 4.4 Multi-agent research ([F5](../evals/results/f5_agent/README.md)): negative
| | single-shot RAG | planner/researchers/drafter/verifier |
|---|---|---|
| correct | **0.843** | 0.725 |
| citation accuracy (verbatim) | 0.644 | **0.920** |
| faithfulness | 0.901 | 0.912 |
| p50 latency | **2.8 s** | 19.1 s |

- **Correctness fell, significantly:** −0.118 [−0.235, −0.02].
- **The pipeline cites much more precisely,** and the verifier step is why.
- **Why it loses.** Of 51 paired questions, the agent alone was wrong on 7:
  - it dropped conditions while compressing evidence into claims;
  - it added requirements the Code lacks. The verifier checks that each claim is supported,
    not that the claims cover only what the sources say;
  - it underused Constitutional Court decisions that override the article text.
- **Retrieval was not the bottleneck.** With Recall@5 already at 0.95, decomposition had
  little to add.
- **The agent ships as an opt-in endpoint;** the baseline stays the default.

### 4.5 Fine-tuning ([F7 embed](../evals/results/f7_embed/README.md), [F7 rerank](../evals/results/f7_rerank/README.md), [F8](../evals/results/f8_verifier/README.md))
**Embedder (negative).** Training on synthetic questions raised validation MRR from 0.726 to
0.806. On the human set, Recall@5 rose by +0.4% against the pre-registered +8%. The base was
already at 0.946, so that target was out of reach.

**Reranker distillation (negative).** A 118M student kept 93% of the 568M teacher's MRR, against
a 95% target. The teacher itself did not significantly beat dense retrieval, which was the
risk stated in advance. int8 ONNX was slower than fp32 PyTorch on this CPU.

**Verifier (positive).** Qwen3.5-2B with LoRA was trained on a free Colab T4, with the base
chosen by tokenizer fertility. Both pre-registered gates passed:

| | local 2B | API (gpt-oss-20b) |
|---|---|---|
| macro-F1 (114 test claims) | 0.935 | 0.951 (retention 0.984, CI [0.91, 1.05]) |
| cost per claim | $1.3e-5 | $9.5e-5 (7.3x) |
| latency | 214 ms | 1.59 s |

It still serves from the API. Self-hosting pays off only above about 0.6 claims/s, and only
after a shadow run on real traffic (ADR-0010).

### 4.6 Platform
**Semantic cache ([F9](../evals/results/f9_cache/README.md)).**

- It saved 23.9% of LLM calls against a 25% target, so it ships off.
- Each cache entry is invalidated by an *evidence fingerprint*: it must be re-retrieved
  before reuse, so a cached answer cannot outlive the law it quotes.

**Durable runs.** Agent runs survive a worker killed mid-step. Temporal resumes from the last
finished activity without repeating paid LLM calls.

**Tenancy and security ([F10 red team](../evals/results/f10_redteam/README.md)).**

- The injection screen blocks 56/57 held-out attacks (98.2%), 0/281 real questions and 0/17
  hard benign items. It is pre-registered and runs in CI.
- 20 attacks that passed the screen were sent straight to the model; it obeyed none of them.
- The OWASP LLM Top 10 audit finds 8 risks controlled and 2 partial.

### 4.7 Contract analysis ([F10](../evals/results/f10_contracts/)): negative
**Setup.**

- The feature reads PDF, DOCX or a photo (OCR), splits it into clauses and checks each clause.
- No real contracts could be obtained. Another model wrote and labelled three test sets
  (10 contracts each), and each label was checked against the Code before scoring.
- The pipeline was frozen before each set and scored once.

| set | precision | recall | right article |
|---|---|---|---|
| development (same author as the rules) | 1.00 | 1.00 | 1.00 |
| held-out 1 | 1.00 | 0.28 | 0.88 |
| held-out 2 | 0.67 | 0.50 | 0.64 |
| held-out 3 (final) | 0.59 | 0.61 | 0.76 |
| *threshold* | *0.80* | *0.70* | *0.80* |

**What failed:**

- **Deterministic rules keyed to phrasings.** They were perfect on their author's contracts
  and brittle on anyone else's.
- **Literal quote verification.** It discarded correct findings that the model had elided.
  Fixed after held-out 2 by verifying each segment.
- **One extracted fact.** A child's birth date, read as the employee's, produced 9 of 12 false
  alarms in the final run, through both the rules and the prompt.

**What held:** the LLM's judgement. Its recall rose every round, and in the last run it cited
the right article on every hit. The feature ships as a research aid with a disclaimer and no
accuracy claim (ADR-0013, revised).

### 4.8 Load ([F11](../evals/results/f11_load/README.md))
**Setup.** One uvicorn worker on a 16-thread CPU, the real stack, and an LLM stub of 1.6 s.

**Results.**

- 0 errors in 3,163 requests.
- At 10 users: search p95 460 ms; ask p95 2.2 s.
- Throughput plateaus at 11.4 req/s from 50 users. bge-m3 query embedding on CPU costs
  88 ms, or 11.4 per second.
- Endpoints that embed nothing stay under 250 ms p99 at 100 users.
- Batched embedding reaches 23 per second, so micro-batching is the cheapest next step.

## 5. Lessons
1. **Measure before adding a component.** Hybrid search, reranking, query rewriting, a
   fine-tuned embedder and a multi-agent pipeline each looked like an upgrade. Each lost to,
   or failed to beat, a simpler baseline on the real question set.
2. **Grounding is a mechanism, not a prompt.** Verbatim quote checking made invented
   citations impossible. Its only real cost was recall, when the check was too literal.
3. **Same-author evaluation proves almost nothing.**
   - The contract rules scored 1.00 on contracts written by their author and 0.28 on another
     author's.
   - The red-team screen states the same bias in its report.
4. **Facts injected into prompts are trusted more than the evidence.** One wrong extracted
   fact outweighed twenty correct clauses.
5. **Freeze before you look, and stop when you said you would.** Three held-out runs with
   pre-committed thresholds and a pre-committed last run kept F10 honest.

## 6. Limitations
- **One law.** Only the Labour Code, with the Constitutional Court decisions that interpret
  it. Contract clauses governed by other laws can only be marked "risk".
- **Uncalibrated judge.** Judge-based correctness is not yet calibrated against human labels
  (κ pending). The labels are written by the author, not by lawyers.
- **Small samples.** Several sets have 30–60 questions, so their confidence intervals are wide
  and are reported with every number.
- **Free-tier LLMs.** Free-tier limits shaped the evaluation schedule. The final contract runs
  used OpenRouter for the same open-weight model.
- **Not legal advice.** The system is a research aid.

## 7. Reproducing
- **Stack:** `docker compose up -d`, then `uv sync`.
- **Evaluations:** each result directory names the command that produced it, for example
  `mizan-eval run`, `mizan-graph gate`, `mizan-eval redteam --gate`, `mizan-eval contracts
  --llm --dataset …` and `python infra/loadtest/run.py`.
- **CI** runs lint, strict typing, about 540 tests, integration tests against Postgres, the
  retrieval gate, the red-team gate and the contract-rules regression gate on every push.
