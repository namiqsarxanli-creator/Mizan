"""Edge derivation, graph-aware ranking and the Neo4j store."""

import os
from collections.abc import AsyncIterator, Collection
from datetime import date

import pytest

from mizan_graph.build import BuildStats, Reference, build_references
from mizan_graph.citations import SELF
from mizan_graph.decisions import Decision
from mizan_graph.search import GraphPolicy, GraphSearcher
from mizan_graph.store import Direction, InMemoryGraph, Neighbour, Neo4jGraph
from mizan_ingestion.chunking import Strategy
from mizan_retrieval.corpus import OPEN_END, ChunkRecord
from mizan_retrieval.search import Hit, Mode, SearchConfig

D1, D2, D3 = date(2000, 1, 1), date(2010, 1, 1), date(2020, 1, 1)
DENSE = SearchConfig(Mode.DENSE, dense_model="bge-m3", strategy=Strategy.ARTICLE)


def record(key: str, body: str, start: date, end: date = OPEN_END) -> ChunkRecord:
    return ChunkRecord(
        46943, Strategy.ARTICLE, key, None, (key,), f"Kod > {key}\n{body}", start, end
    )


# ------------------------------------------------------------------ build


def test_edges_carry_merged_validity_intervals() -> None:
    records = [
        # Art. 77 cites Art. 70 in two consecutive texts, then stops citing it.
        record("art:77", "bu Məcəllənin 70-ci maddəsinin “b” bəndi", D1, D2),
        record("art:77", "bu Məcəllənin 70-ci maddəsi ilə", D2, D3),
        record("art:77", "yeni mətn", D3),
        record("art:70", "əsaslar", D1),
    ]

    refs = build_references(records)

    assert refs == [Reference("art:77", SELF, "art:70", ("70",), D1, D3)]


def test_self_citations_are_dropped_and_absent_targets_reported() -> None:
    stats = BuildStats()
    records = [
        record("art:10-1", "Bu Məcəllənin 10-1.2-ci maddəsində və 999-cu maddəsində", D1),
    ]

    refs = build_references(records, stats=stats)

    assert stats.self_loops == 1
    assert stats.unresolved == [("art:10-1", "999", D1)]
    assert [r.target for r in refs] == ["art:999"]


def test_breadcrumb_line_is_not_searched_for_citations() -> None:
    rec = ChunkRecord(
        46943, Strategy.ARTICLE, "art:5", None, (), "Kod > 3-cü maddə başlığı\nmətn", D1, OPEN_END
    )
    assert build_references([rec]) == []


# ------------------------------------------------------------------ ranking


def hit(article: str, score: float) -> Hit:
    return Hit(article, article, article, f"{article}\nmətn", score, D1, (article,))


class FakeDense:
    def __init__(self, ranked: list[Hit]) -> None:
        self.ranked = ranked
        self.calls: list[Collection[str] | None] = []

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
        self.calls.append(only_articles)
        hits = [h for h in self.ranked if only_articles is None or h.article_key in only_articles]
        return hits[:k]


class BrokenGraph:
    async def neighbours(
        self, source_id: int, keys: Collection[str], *, as_of: date, direction: Direction
    ) -> list[Neighbour]:
        raise ConnectionError("neo4j is down")


# Art. 77 cites Art. 70 (similarity rank 9) and Art. 68 (rank 7).
GRAPH = InMemoryGraph(
    46943,
    [
        Reference("art:77", SELF, "art:70", ("70",), D1, OPEN_END),
        Reference("art:77", SELF, "art:68", ("68",), D1, OPEN_END),
        Reference("art:77", "konstitusiya", "art:25", ("25",), D1, OPEN_END),
    ],
)
RANKED = [hit(f"art:{n}", 0.9 - i * 0.01) for i, n in enumerate([77, 1, 2, 3, 4, 5, 68, 6, 70])]


async def search(policy: GraphPolicy, query: str = "ixtisar müavinəti", k: int = 5) -> list[str]:
    searcher = GraphSearcher(FakeDense(RANKED), GRAPH, policy)
    hits = await searcher.search(query, source_id=46943, as_of=D2, config=DENSE, k=k)
    return [h.article_key for h in hits]


async def test_slot_policy_keeps_the_head_and_reserves_a_place_for_a_cited_article() -> None:
    ranked = await search(GraphPolicy(seeds=1, slots=1, window=5))
    # The four best dense articles stay; the most similar article cited by Art. 77 follows.
    assert ranked == ["art:77", "art:1", "art:2", "art:3", "art:68"]


async def test_two_slots() -> None:
    ranked = await search(GraphPolicy(seeds=1, slots=2, window=5))
    assert ranked == ["art:77", "art:1", "art:2", "art:68", "art:70"]


async def test_boost_policy_rescores_neighbours() -> None:
    ranked = await search(GraphPolicy(seeds=1, boost=0.05), k=6)
    assert ranked == ["art:77", "art:68", "art:1", "art:2", "art:70", "art:3"]


async def test_external_acts_are_not_neighbours() -> None:
    ranked = await search(GraphPolicy(seeds=1, slots=2), k=10)
    assert "art:25" not in ranked


async def test_a_cited_article_in_the_question_ranks_first() -> None:
    ranked = await search(GraphPolicy(seeds=0), query="Əmək Məcəlləsinin 6-cı maddəsi nədir?")
    assert ranked[0] == "art:6"


async def test_neighbours_outside_the_candidates_are_fetched() -> None:
    dense = FakeDense(RANKED)
    searcher = GraphSearcher(dense, GRAPH, GraphPolicy(seeds=1, slots=1))
    config = SearchConfig(Mode.DENSE, dense_model="bge-m3", candidates=3)

    hits = await searcher.search("q", source_id=46943, as_of=D2, config=config, k=5)

    assert dense.calls[1] is not None
    assert set(dense.calls[1]) == {"art:68", "art:70"}
    assert [h.article_key for h in hits] == ["art:77", "art:1", "art:2", "art:3", "art:68"]


async def test_graph_outage_falls_back_to_dense() -> None:
    searcher = GraphSearcher(FakeDense(RANKED), BrokenGraph(), GraphPolicy(seeds=1, slots=1))
    hits = await searcher.search("q", source_id=46943, as_of=D2, config=DENSE, k=5)
    assert [h.article_key for h in hits] == [h.article_key for h in RANKED[:5]]


async def test_fused_scores_are_rejected() -> None:
    searcher = GraphSearcher(FakeDense(RANKED), GRAPH, GraphPolicy())
    hybrid = SearchConfig(Mode.HYBRID)
    with pytest.raises(ValueError, match="dense"):
        await searcher.search("q", source_id=46943, as_of=D2, config=hybrid)


async def test_edges_outside_their_interval_are_ignored() -> None:
    graph = InMemoryGraph(46943, [Reference("art:77", SELF, "art:70", ("70",), D2, D3)])
    before = await graph.neighbours(46943, ["art:77"], as_of=D1, direction=Direction.OUT)
    during = await graph.neighbours(46943, ["art:77"], as_of=D2, direction=Direction.OUT)
    incoming = await graph.neighbours(46943, ["art:70"], as_of=D2, direction=Direction.IN)
    assert before == []
    assert during == [Neighbour("art:77", "art:70", Direction.OUT)]
    assert incoming == [Neighbour("art:70", "art:77", Direction.IN)]


# ------------------------------------------------------------------ Neo4j

integration = pytest.mark.skipif(
    os.environ.get("MIZAN_INTEGRATION") != "1",
    reason="set MIZAN_INTEGRATION=1 with the compose stack running",
)
TEST_SOURCE = 999_000_001  # not an e-qanun id: never collides with real data


@pytest.fixture
async def neo4j() -> AsyncIterator[Neo4jGraph]:
    from neo4j import AsyncGraphDatabase

    from mizan_core.config import get_settings

    settings = get_settings()
    driver = AsyncGraphDatabase.driver(
        settings.neo4j.uri,
        auth=(settings.neo4j.user, settings.neo4j.password.get_secret_value()),
    )
    graph = Neo4jGraph(driver)
    await graph.ensure_schema()
    try:
        yield graph
    finally:
        await driver.execute_query(
            "MATCH (n) WHERE n.uid STARTS WITH $prefix OR n.act = $prefix DETACH DELETE n",
            prefix=f"eqanun:{TEST_SOURCE}",
        )
        await driver.close()


@integration
@pytest.mark.integration
async def test_neo4j_answers_like_the_in_memory_graph(neo4j: Neo4jGraph) -> None:
    refs = [
        Reference("art:77", SELF, "art:70", ("70",), D1, D3),
        Reference("art:77", SELF, "art:68", ("68",), D2, OPEN_END),
        Reference("art:79", SELF, "art:70", ("70",), D1, OPEN_END),
        Reference("art:77", "konstitusiya", "art:25", ("25",), D1, OPEN_END),
    ]
    memory = InMemoryGraph(TEST_SOURCE, refs)
    await neo4j.replace_references(TEST_SOURCE, "test", refs)
    await neo4j.replace_references(TEST_SOURCE, "test", refs)  # idempotent

    for as_of in (D1, D2, D3):
        for direction in Direction:
            keys = ["art:77", "art:70"]
            got = await neo4j.neighbours(TEST_SOURCE, keys, as_of=as_of, direction=direction)
            want = await memory.neighbours(TEST_SOURCE, keys, as_of=as_of, direction=direction)
            assert sorted(got, key=repr) == sorted(want, key=repr), (as_of, direction)


@integration
@pytest.mark.integration
async def test_neo4j_decisions_match_the_in_memory_store(neo4j: Neo4jGraph) -> None:
    decisions = [
        Decision(999_000_101, "t1", D2, "interpretation", "op1", ("art:172", "art:296")),
        Decision(999_000_102, "t2", D3, "unconstitutional", "op2", ("art:144",)),
    ]
    memory = InMemoryGraph(TEST_SOURCE, [], decisions)
    await neo4j.replace_decisions(TEST_SOURCE, decisions)
    await neo4j.replace_decisions(TEST_SOURCE, decisions)  # idempotent

    for as_of in (D1, D2, D3):
        for keys in (["art:296"], ["art:144", "art:172"], ["art:1"]):
            got = await neo4j.decisions_for(TEST_SOURCE, keys, as_of=as_of)
            want = await memory.decisions_for(TEST_SOURCE, keys, as_of=as_of)
            assert _ids(got) == _ids(want), (as_of, keys)


def _ids(decisions: list[Decision]) -> list[tuple[int, list[str]]]:
    return sorted((d.eqanun_id, sorted(d.interprets)) for d in decisions)
