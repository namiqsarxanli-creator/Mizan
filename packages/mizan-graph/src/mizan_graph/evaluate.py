"""Evaluate graph-aware retrieval against dense retrieval.

The protocol is pre-registered in ``evals/results/f4_graph/PREREGISTRATION.md``: the policy
under test (:data:`PRIMARY`) was fixed before the test set ``multihop_b.jsonl`` was run. The
first protocol, automatic selection over the grid by 35 questions, picked a policy that did not
transfer (winner's curse); :func:`select_by_focus` is kept to reproduce that finding.

Every policy re-ranks the same dense candidates, so the dense search runs once per question
(:class:`PrecomputedBackend`) and a policy grid costs graph queries only.
"""

from __future__ import annotations

import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date

from mizan_graph.search import SERVING_POLICY, GraphPolicy, GraphSearcher
from mizan_graph.store import Direction, GraphStore
from mizan_retrieval.evaluation import Question, RunResult, paired_bootstrap
from mizan_retrieval.search import Hit, SearchBackend, SearchConfig

LABOUR_CODE = 46943
BASELINE = GraphPolicy(seeds=0, route_citations=False)
PRIMARY = SERVING_POLICY
SECONDARY = GraphPolicy(seeds=1, direction=Direction.OUT, slots=2, window=5)


class PrecomputedBackend:
    """Serves searches for one query from its full ranked list."""

    def __init__(self, query: str, ranked: Sequence[Hit]) -> None:
        self._query = query
        self._ranked = list(ranked)

    async def search(
        self,
        query: str,
        *,
        source_id: int,
        as_of: date,
        config: SearchConfig,
        k: int = 10,
        only_articles: Collection[str] | None = None,
    ) -> list[Hit]:
        if query != self._query:
            raise ValueError("precomputed for another query")
        hits = self._ranked
        if only_articles is not None:
            hits = [h for h in hits if h.article_key in only_articles]
        return hits[:k]


def policy_grid() -> list[GraphPolicy]:
    grid = [BASELINE, GraphPolicy(seeds=0)]  # dense; dense + cited-article routing
    for direction in (Direction.OUT, Direction.BOTH):
        for seeds in (1, 2, 3):
            grid += [GraphPolicy(seeds, direction, boost=b) for b in (0.02, 0.05, 0.1)]
        for seeds in (1, 2):
            grid += [GraphPolicy(seeds, direction, slots=n) for n in (1, 2)]
    return grid


@dataclass(frozen=True, slots=True)
class Ranked:
    question: Question
    hits: list[Hit]
    latency_ms: float


async def precompute(
    base: SearchBackend, questions: Sequence[Question], config: SearchConfig, *, depth: int = 2000
) -> list[Ranked]:
    """Every chunk valid on the question's date, ranked by similarity (one search each)."""
    out = []
    for q in questions:
        if not q.relevant:
            continue
        started = time.perf_counter()
        hits = await base.search(
            q.question, source_id=LABOUR_CODE, as_of=q.as_of, config=config, k=depth
        )
        out.append(Ranked(q, hits, (time.perf_counter() - started) * 1000))
    return out


async def run_policy(
    policy: GraphPolicy, ranked: Sequence[Ranked], graph: GraphStore, config: SearchConfig
) -> RunResult:
    result = RunResult(policy.label)
    for r in ranked:
        searcher = GraphSearcher(PrecomputedBackend(r.question.question, r.hits), graph, policy)
        started = time.perf_counter()
        hits = await searcher.search(
            r.question.question,
            source_id=LABOUR_CODE,
            as_of=r.question.as_of,
            config=config,
            k=10,
        )
        graph_ms = (time.perf_counter() - started) * 1000
        result.add(r.question, [h.article_key for h in hits], r.latency_ms + graph_ms)
    return result


def select_by_focus(
    results: dict[GraphPolicy, RunResult], questions: Sequence[Question], *, focus: str = "multi"
) -> GraphPolicy:
    """Protocol 1: best Recall@5 on ``focus`` questions among policies doing no significant harm.

    Superseded: with 35 focus questions and a grid of 28 policies it selects noise.
    """
    baseline = results[BASELINE]
    focus_ids = [q.id for q in questions if q.type == focus and q.id in baseline.per_question]

    def harmless(policy: GraphPolicy) -> bool:
        comparisons = paired_bootstrap(baseline.per_question, results[policy].per_question)
        return not any(c.significantly_worse for c in comparisons)

    def key(policy: GraphPolicy) -> tuple[float, float]:
        s = results[policy].summary(focus_ids)
        overall = results[policy].summary()
        return (s["Recall@5"].mean, overall["MRR@10"].mean)

    candidates = [p for p in results if harmless(p)]
    return max(candidates, key=key)
