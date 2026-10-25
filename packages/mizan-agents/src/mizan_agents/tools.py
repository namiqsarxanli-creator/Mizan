"""The tools agents use. Every tool is deterministic; none of them calls an LLM.

| Tool                     | Backed by                                   |
|--------------------------|---------------------------------------------|
| ``search_law``           | dense search + citation graph (F2, F4)      |
| ``get_article``          | the article's full text on a date (Qdrant)  |
| ``get_related_articles`` | citation edges in force on a date (Neo4j)   |
| ``get_court_decisions``  | Constitutional Court decisions (Neo4j)      |
| ``check_conflicts``      | NHA collision rules over graph facts        |

Researchers compose them without an LLM: which tool to call next is not a judgement call for
a Labour Code question, so spending a model call (and its latency, cost and failure modes) on
it buys nothing. LLMs are used where judgement is needed: planning, drafting, verification.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date

from mizan_agents.schemas import Evidence, EvidenceKind
from mizan_core.genai import tool_span
from mizan_graph.conflicts import Force, Norm, Relation, Resolution, resolve, specialis_pairs
from mizan_graph.decisions import Decision, decision_hit
from mizan_graph.store import DecisionStore, Direction, GraphStore, act_uid
from mizan_retrieval.answer import source_title
from mizan_retrieval.search import Hit, SearchBackend, SearchConfig

# Whole articles for hits (parent-child retrieval), as in the API.
Expander = Callable[[Sequence[Hit], int, date], Awaitable[list[Hit]]]

LABOUR_CODE_ADOPTED = date(1999, 2, 1)


def evidence(hit: Hit, *, via: str) -> Evidence:
    kind = EvidenceKind.DECISION if hit.key.startswith("kmq:") else EvidenceKind.ARTICLE
    return Evidence(
        key=hit.article_key,
        title=source_title(hit),
        text=hit.text,
        kind=kind,
        in_force_since=hit.valid_from,
        via=via,
    )


def _stub(key: str, on: date) -> Hit:
    return Hit(key, key, key, "", 0.0, on, ())


@dataclass(slots=True)
class LegalTools:
    searcher: SearchBackend
    config: SearchConfig
    expand: Expander
    graph: GraphStore
    decisions: DecisionStore
    source_id: int = 46943

    async def search_law(self, query: str, *, as_of: date, k: int = 5) -> list[Evidence]:
        with tool_span("search_law", **{"mizan.k": k}) as span:
            hits = await self.searcher.search(
                query, source_id=self.source_id, as_of=as_of, config=self.config, k=k
            )
            articles = await self.expand(hits, self.source_id, as_of)
            span.set_attribute("mizan.results", [h.article_key for h in articles])
            return [evidence(h, via="search") for h in articles]

    async def get_article(self, key: str, *, as_of: date) -> Evidence | None:
        with tool_span("get_article", **{"mizan.key": key}):
            return await self._get_article(key, as_of=as_of)

    async def _get_article(self, key: str, *, as_of: date) -> Evidence | None:
        found = await self.expand([_stub(key, as_of)], self.source_id, as_of)
        texts = [h for h in found if h.text]
        return evidence(texts[0], via="cited") if texts else None

    async def get_related_articles(
        self, key: str, *, as_of: date, direction: Direction = Direction.OUT
    ) -> list[str]:
        with tool_span("get_related_articles", **{"mizan.key": key}):
            found = await self.graph.neighbours(
                self.source_id, [key], as_of=as_of, direction=direction
            )
            return sorted({n.key for n in found})

    async def get_court_decisions(self, keys: Sequence[str], *, as_of: date) -> list[Decision]:
        with tool_span("get_court_decisions") as span:
            found = await self.decisions.decisions_for(self.source_id, keys, as_of=as_of)
            span.set_attribute("mizan.results", [d.key for d in found])
            return found

    async def check_conflicts(
        self, keys: Sequence[str], decisions: Sequence[Decision], *, as_of: date
    ) -> list[Resolution]:
        """Collisions the graph can see among ``keys``: explicit exceptions inside the Code
        (lex specialis) and provisions the Constitutional Court suspended or voided."""
        with tool_span("check_conflicts") as span:
            results = await self._conflicts(keys, decisions, as_of=as_of)
            span.set_attribute("mizan.results", [r.rule.value for r in results])
            return results

    async def _conflicts(
        self, keys: Sequence[str], decisions: Sequence[Decision], *, as_of: date
    ) -> list[Resolution]:
        pairs = await self.graph.exceptions(self.source_id, keys, as_of=as_of)
        voided = {
            key: d
            for d in decisions
            if d.outcome == "unconstitutional" and d.adopted_on <= as_of
            for key in d.interprets
        }

        def norm(key: str) -> Norm:
            d = voided.get(key)
            return Norm(
                act=act_uid(self.source_id),
                provision=key,
                force=Force.LAW,
                body="milli məclis",
                adopted_on=LABOUR_CODE_ADOPTED,
                field="əmək",
                voided_by=f"Konstitusiya Məhkəməsinin {d.adopted_on} qərarı" if d else None,
                voided_on=d.adopted_on if d else None,
            )

        relation = Relation(as_of, field="əmək", special_over=specialis_pairs(pairs))
        results = [resolve(norm(general), norm(special), relation) for general, special in pairs]
        # A suspended or voided provision loses to the Constitution the Court applied.
        results += [resolve(norm(key), CONSTITUTION, relation) for key in voided if key in keys]
        return results


CONSTITUTION = Norm(
    act="eqanun:897",
    provision="Konstitusiya",
    force=Force.CONSTITUTION,
    body="referendum",
    adopted_on=date(1995, 11, 12),
)


def decision_evidence(decisions: Sequence[Decision]) -> list[Evidence]:
    return [evidence(decision_hit(d), via="decisions") for d in decisions]
