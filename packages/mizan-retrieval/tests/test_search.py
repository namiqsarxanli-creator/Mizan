"""Search pipeline against Qdrant's in-process engine with a deterministic fake embedder."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import date

import numpy as np
import pytest
from qdrant_client import AsyncQdrantClient

from mizan_ingestion.chunking import Strategy
from mizan_retrieval.analysis import Analyzer
from mizan_retrieval.corpus import OPEN_END, ChunkRecord
from mizan_retrieval.embeddings import Embedder, ModelSpec, Vector
from mizan_retrieval.index import alias_for, build_index
from mizan_retrieval.search import Mode, SearchConfig, Searcher

DIM = 64


class BagOfWordsEmbedder(Embedder):
    """Hashes stemmed tokens into a normalised vector: similar texts get similar vectors."""

    def __init__(self) -> None:
        super().__init__(ModelSpec("fake", "none", DIM))
        self._analyzer = Analyzer(prefix=5)

    def _encode(self, texts: list[str]) -> Vector:
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in self._analyzer.tokens(text):
                bucket = int(hashlib.md5(token.encode()).hexdigest(), 16) % DIM  # noqa: S324
                out[row, bucket] += 1.0
            norm = np.linalg.norm(out[row])
            if norm:
                out[row] /= norm
        return out


def record(key: str, text: str, start: date, end: date = OPEN_END) -> ChunkRecord:
    return ChunkRecord(46943, Strategy.ARTICLE, key, key, (key,), text, start, end)


OLD, NEW = date(2000, 1, 1), date(2025, 6, 13)
RECORDS = [
    record("art:51", "Sınaq müddəti üç aydan çox ola bilməz.", OLD),
    record(
        "art:114", "Əsas məzuniyyətin müddəti iş ilinə görə 21 təqvim günündən az olmamalıdır.", OLD
    ),
    record("art:172", "Əmək haqqı ayda bir dəfədən az olmayaraq verilir.", OLD),
    # Article 69 changed in 2025: two records with disjoint validity.
    record("art:69", "İşçi işəgötürəni yazılı ərizəsi ilə xəbərdar edir.", OLD, NEW),
    record("art:69", "İşçi işəgötürəni elektron sistem vasitəsilə ərizə ilə xəbərdar edir.", NEW),
]


@pytest.fixture
async def client() -> AsyncIterator[AsyncQdrantClient]:
    c = AsyncQdrantClient(location=":memory:")
    yield c
    await c.close()


@pytest.fixture
async def searcher(client: AsyncQdrantClient) -> Searcher:
    embedder = BagOfWordsEmbedder()
    await build_index(client, Strategy.ARTICLE, RECORDS, [embedder])
    return Searcher(client, {"fake": embedder})


@pytest.mark.parametrize("mode", list(Mode))
async def test_every_mode_finds_the_relevant_article(searcher: Searcher, mode: Mode) -> None:
    config = SearchConfig(mode=mode, dense_model="fake", sparse="bm25_p5")
    hits = await searcher.search(
        "sınaq müddəti neçə aydır", source_id=46943, as_of=date(2026, 1, 1), config=config, k=3
    )
    assert hits[0].article_key == "art:51"


async def test_temporal_filter_returns_the_text_in_force(searcher: Searcher) -> None:
    config = SearchConfig(mode=Mode.HYBRID, dense_model="fake")

    before = await searcher.search(
        "ərizə xəbərdar", source_id=46943, as_of=date(2020, 1, 1), config=config, k=5
    )
    after = await searcher.search(
        "ərizə xəbərdar", source_id=46943, as_of=date(2026, 1, 1), config=config, k=5
    )

    [old] = [h for h in before if h.article_key == "art:69"]
    [new] = [h for h in after if h.article_key == "art:69"]
    assert "yazılı" in old.text
    assert "elektron" in new.text
    assert new.valid_from == NEW


async def test_nothing_is_returned_before_the_law_existed(searcher: Searcher) -> None:
    config = SearchConfig(mode=Mode.HYBRID, dense_model="fake")
    assert not await searcher.search(
        "sınaq", source_id=46943, as_of=date(1990, 1, 1), config=config
    )


async def test_stopword_only_query_has_no_lexical_hits(searcher: Searcher) -> None:
    config = SearchConfig(mode=Mode.SPARSE, dense_model="fake")
    assert await searcher.search("və ya", source_id=46943, as_of=NEW, config=config) == []


async def test_rebuild_swaps_alias_and_drops_old_collection(client: AsyncQdrantClient) -> None:
    embedder = BagOfWordsEmbedder()
    first = await build_index(client, Strategy.ARTICLE, RECORDS[:2], [embedder])
    second = await build_index(client, Strategy.ARTICLE, RECORDS, [embedder])

    collections = {c.name for c in (await client.get_collections()).collections}
    aliases = {a.alias_name: a.collection_name for a in (await client.get_aliases()).aliases}
    assert aliases[alias_for(Strategy.ARTICLE)] == second
    assert first not in collections
    assert (await client.count(alias_for(Strategy.ARTICLE))).count == len(RECORDS)
