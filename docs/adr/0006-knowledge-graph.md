# ADR-0006: Knowledge graph of citations, and Neo4j to store it

- **Status:** Accepted
- **Date:** 2026-10-18
- **Phase:** F4

## Context
Legal provisions depend on each other. Art. 77 (severance) applies to dismissals "under
Art. 70 'b'"; Art. 144 (leave compensation) excludes the leave of Arts. 115–117. A question
about severance on redundancy matches Art. 77 well and Art. 70 poorly (similarity rank 57), so
similarity search alone misses half of the answer.

The Labour Code cites itself 618 times in its current text, plus the Constitution, three other
codes and four laws. Citations change over time with the text that contains them.

Questions this ADR answers: how to extract citations, how to store the graph, how retrieval
uses it, and how we know it helps.

## Decision

### 1. Citations are extracted by a deterministic grammar, not an LLM
The roadmap planned LLM extraction. The citation grammar of Azerbaijani codes turned out to be
small and regular (lists, en-dash ranges, inserted articles "118-1", dotted points "10-1.2",
act qualifiers "bu Məcəllənin", "Vergi Məcəlləsinin", "“...” Qanununun", "həmin"), so a
grammar handles it exactly, for free and reproducibly (`mizan_graph/citations.py`, checked
against labelled sentences from the Code). On the current corpus: 633 citations, 216 edges,
**0 citations of an article that does not exist** on the citing text's date. An LLM remains
the plan for other acts' messier styles and for court decisions (F4 week 9, F8).

### 2. Edges are temporal
An edge `(Art. 77) -[:REFERENCES]-> (Art. 70)` carries the union of the validity intervals of
the texts that contain the citation, merged into maximal ranges (day ordinals, as in Qdrant).
"Neighbours on 2015-03-01" is one integer comparison, and a citation added by a 2022 amendment
does not leak into a 2015 question.

### 3. Storage: Neo4j, as derived and rebuildable data
| Option | For | Against |
|---|---|---|
| Postgres recursive CTE | one store, transactional with ingestion | path and pattern queries are verbose; no graph tooling |
| In-process (networkx) | zero ops | per-process memory, no shared queries for agents |
| **Neo4j** | native traversal and variable-length paths, Cypher for the F5 agents, browser to inspect | one more stateful service; consistency with Postgres |

At today's size (170 articles, 216 edges) Postgres would be fully adequate, and we say so. We
choose Neo4j for what F4 week 9 and F5 need: amendment lineage, chains of special and general
rules for contradiction detection, multi-act paths, and an agent tool that queries the graph.

The cost is contained by three rules:
- **The graph is derived data.** Postgres and the corpus snapshot are the source of truth;
  `mizan-graph build` rebuilds the edges of an act idempotently.
- **A `GraphStore` protocol** isolates the store. `InMemoryGraph`, built from the snapshot,
  answers the same queries; an integration test checks the two agree on every date and
  direction. CI's eval gate uses it and needs no Neo4j. A Postgres implementation would be a
  small, local change.
- **The graph improves search but never breaks it.** If Neo4j is unreachable, graph-aware
  search logs a warning and returns dense results.

### 4. Retrieval: one reserved place for a cited article
`GraphSearcher` wraps dense search: the best article is the seed, and the place at rank 5 goes
to the article it cites that is most similar to the question (`seeds=1, slots=1/5`). Articles
cited explicitly in the question ("69-cu maddə") are ranked first.

A similarity *boost* was tried first and fails structurally: a cited article sits 0.1–0.3 below
the seed in cosine, more than any boost that leaves other rankings intact.

### 5. How we know: a pre-registered test
1. Protocol 1 chose a policy automatically by Recall@5 on v1's 35 `multi` questions, out of 28.
   It chose a boost policy that did nothing on the 40-question multi-hop set (0.838 → 0.838)
   and lowered MRR. This is the winner's curse: 35 questions cannot rank 28 policies.
2. The slot policy was then **registered before** a fresh 30-question set (`multihop_b`) was
   run (`evals/results/f4_graph/PREREGISTRATION.md`, committed first).

| Dataset | Dense Recall@5 | + graph | Paired Δ [95% CI] | MRR |
|---|---|---|---|---|
| `multihop_b` (pre-registered test, 30) | 0.700 | **0.817** | +0.117 [+0.05, +0.20] | unchanged (0.836) |
| `multihop` (where the policy was found, 40) | 0.838 | 0.912 | +0.075 [+0.03, +0.14] | unchanged (0.911) |
| v1 (written without the graph, 185) | 0.948 | 0.950 | +0.003 [-0.01, +0.02] | 0.894 → 0.901 (n.s.) |
| v0 gate set (70) | 0.929 | 0.919 | -0.010 [-0.04, +0.01] | 0.831 → 0.830 (n.s.) |

On the test set the gain is **+16.7% relative**, above the F4 target of 10%. Elsewhere there is
no significant change. Cost: about 8 ms p50 (one Neo4j query and, sometimes, one Qdrant query).

## Consequences
- The serving path is dense search plus the graph (`MIZAN_RETRIEVAL__GRAPH=true`); the CI gate
  protects that path (`mizan-graph gate`).
- The multi-hop sets are built from graph edges. They show the effect where citations matter,
  not how often users ask such questions; v1 covers the rest.
- Limitations:
  - Citations of other acts are nodes without text until those acts are ingested.
  - Intra-article references ("bu maddənin 2-ci hissəsi") are not edges.
  - Part-level targets ("70-ci maddənin 'b' bəndi") are kept as cited numbers on the edge, not
    resolved to the part.
