"""Graph-aware retrieval: follow citations from the best articles.

    query ─ dense search ─ top articles (seeds) ─ citation neighbours in force on as_of
                │                                          │
                └──────────── merge (boost or slots) ─────────── ranked articles

A question about severance on redundancy matches Art. 77 well, but the grounds it applies to
are in Art. 70, which the question never paraphrases: Art. 70 ranks 57th by similarity. Art. 77
cites Art. 70, so the graph can bring it back. Two ways to merge, chosen by evaluation:

* **boost**: add a constant to a neighbour's own similarity. Gentle, but a cited article is
  often 0.1-0.3 below the seeds in cosine, more than any boost that does not reorder
  everything else.
* **slots**: reserve the last ``slots`` places of the top ``window`` for the neighbours most
  similar to the query. Ranks above the slots are untouched, so precision at the top (MRR)
  is kept; the reserved places trade a weak dense candidate for a cited one.

Explicit citations in the question ("69-cu maddə nə barədədir?") are routed directly: the cited
article is ranked first, whatever its similarity.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import date

from mizan_core.logging import get_logger
from mizan_graph.citations import extract
from mizan_graph.store import Direction, GraphStore
from mizan_retrieval.search import Hit, Mode, SearchBackend, SearchConfig

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class GraphPolicy:
    seeds: int = 1  # expand the citations of the top-n articles
    direction: Direction = Direction.OUT
    boost: float = 0.0  # boost mode: added to a neighbour's own cosine similarity
    slots: int = 0  # slot mode (when > 0): places reserved for neighbours in the top window
    window: int = 5
    route_citations: bool = True  # an article cited in the question ranks first

    @property
    def expands(self) -> bool:
        return self.seeds > 0 and (self.boost > 0 or self.slots > 0)

    @property
    def label(self) -> str:
        parts = []
        if self.expands:
            merge = f"slots={self.slots}/{self.window}" if self.slots else f"boost={self.boost}"
            parts.append(f"graph {self.direction.value} seeds={self.seeds} {merge}")
        if self.route_citations:
            parts.append("cited-article routing")
        return " + ".join(parts) or "no graph"


# Pre-registered in evals/results/f4_graph/PREREGISTRATION.md and confirmed on a fresh test set.
SERVING_POLICY = GraphPolicy(seeds=1, direction=Direction.OUT, slots=1, window=5)


class GraphSearcher:
    """A :class:`SearchBackend` that wraps dense search with citation expansion."""

    def __init__(self, base: SearchBackend, graph: GraphStore, policy: GraphPolicy) -> None:
        self._base = base
        self._graph = graph
        self.policy = policy

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
        if config.mode is not Mode.DENSE:
            # Boosts are on the cosine scale; fused (RRF) scores have no comparable scale.
            raise ValueError("graph-aware search rescores dense similarities only")
        p = self.policy
        hits = await self._base.search(
            query,
            source_id=source_id,
            as_of=as_of,
            config=config,
            k=max(k, config.candidates),
            only_articles=only_articles,
        )
        best: dict[str, Hit] = {}
        for h in hits:  # ranked: the first chunk of an article is its best
            best.setdefault(h.article_key, h)

        async def add_missing(keys: Collection[str]) -> None:
            """Neighbours outside the candidates still need their own similarity."""
            missing = [key for key in keys if key not in best]
            if missing:
                extra = await self._base.search(
                    query,
                    source_id=source_id,
                    as_of=as_of,
                    config=config,
                    k=len(missing) * 4,
                    only_articles=missing,
                )
                for h in extra:
                    best.setdefault(h.article_key, h)

        neighbours: set[str] = set()
        if p.expands:
            seeds = list(best)[: p.seeds]
            try:
                found = await self._graph.neighbours(
                    source_id, seeds, as_of=as_of, direction=p.direction
                )
            except Exception:  # the graph improves search; its outage must not break it
                log.warning("graph_unavailable", exc_info=True)
                found = []
            neighbours = {n.key for n in found} - set(seeds)
            if only_articles is not None:
                neighbours &= set(only_articles)
            await add_missing(neighbours)
            neighbours &= best.keys()  # a cited article not valid on as_of has no text

        routed: list[str] = []
        if p.route_citations:
            cited = list(dict.fromkeys(f"art:{c.article}" for c in extract(query) if c.internal))
            await add_missing(cited)
            routed = [key for key in cited if key in best]

        bonus = p.boost if not p.slots else 0.0
        scored = {key: h.score + (bonus if key in neighbours else 0.0) for key, h in best.items()}
        order = sorted(best, key=lambda key: scored[key], reverse=True)
        if p.slots and neighbours:
            order = _reserve_slots(order, neighbours, scored, slots=p.slots, window=p.window)
        order = routed + [key for key in order if key not in routed]
        return [replace(best[key], score=scored[key]) for key in order[:k]]


def _reserve_slots(
    order: list[str], neighbours: set[str], scored: dict[str, float], *, slots: int, window: int
) -> list[str]:
    """Keep the first ``window - slots`` articles; fill the next places with neighbours."""
    keep = order[: window - slots]
    chosen = sorted(
        (key for key in neighbours if key not in keep), key=lambda key: scored[key], reverse=True
    )[:slots]
    head = keep + chosen
    return head + [key for key in order if key not in head]
