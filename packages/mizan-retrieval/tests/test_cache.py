"""Semantic answer cache: threshold, evidence fingerprint, version and TTL (in-process Qdrant)."""

from datetime import date

import numpy as np
import pytest
from qdrant_client import AsyncQdrantClient

from mizan_retrieval.answer import Answer, Citation
from mizan_retrieval.cache import SemanticCache, fingerprint, still_valid
from mizan_retrieval.search import Hit

DIM = 4


def hit(key: str, since: date = date(2021, 5, 27)) -> Hit:
    return Hit(key, key.split("/")[0], key.split("/")[0], f"text of {key}", 0.9, since, ())


ANSWER = Answer(
    text="Sınaq müddəti ən çox üç aydır.",
    citations=(Citation("art:51", "Maddə 51", "ən çoxu üç ay"),),
    model="openai/gpt-oss-120b",
    stop_reason="stop",
)


def unit(*values: float) -> list[float]:
    v = np.array(values, dtype=np.float32)
    return list(v / np.linalg.norm(v))


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def cache(clock: Clock) -> SemanticCache:
    return SemanticCache(
        AsyncQdrantClient(":memory:"), dim=DIM, threshold=0.9, ttl_s=3600, version="p1", now=clock
    )


async def test_a_close_question_with_the_same_sources_is_served(cache: SemanticCache) -> None:
    await cache.store(
        unit(1, 0, 0, 0), "Sınaq nə qədərdir?", ANSWER, [hit("art:51"), hit("art:52")]
    )

    found = await cache.lookup(unit(1, 0.1, 0, 0), [hit("art:51"), hit("art:53")])

    assert found is not None
    assert found.answer.text == ANSWER.text
    assert found.answer.citations == ANSWER.citations
    assert found.answer.stop_reason == "cache"
    assert found.question == "Sınaq nə qədərdir?"
    assert found.similarity > 0.9


async def test_a_different_question_is_not_served(cache: SemanticCache) -> None:
    await cache.store(unit(1, 0, 0, 0), "q", ANSWER, [hit("art:51")])
    assert await cache.lookup(unit(1, 1, 0, 0), [hit("art:51")]) is None  # cosine 0.71


async def test_an_amended_source_invalidates_the_entry(cache: SemanticCache) -> None:
    await cache.store(unit(1, 0, 0, 0), "q", ANSWER, [hit("art:51")])
    amended = [hit("art:51", since=date(2026, 7, 14))]
    assert await cache.lookup(unit(1, 0, 0, 0), amended) is None


async def test_the_cited_source_must_be_retrieved_again(cache: SemanticCache) -> None:
    await cache.store(unit(1, 0, 0, 0), "q", ANSWER, [hit("art:51")])
    assert await cache.lookup(unit(1, 0, 0, 0), [hit("art:52")]) is None


async def test_entries_expire(cache: SemanticCache, clock: Clock) -> None:
    await cache.store(unit(1, 0, 0, 0), "q", ANSWER, [hit("art:51")])
    clock.t += 3601
    assert await cache.lookup(unit(1, 0, 0, 0), [hit("art:51")]) is None


async def test_another_prompt_version_does_not_reuse_entries(clock: Clock) -> None:
    client = AsyncQdrantClient(":memory:")
    old = SemanticCache(client, dim=DIM, threshold=0.9, ttl_s=3600, version="p1", now=clock)
    new = SemanticCache(client, dim=DIM, threshold=0.9, ttl_s=3600, version="p2", now=clock)
    await old.store(unit(1, 0, 0, 0), "q", ANSWER, [hit("art:51")])
    assert await new.lookup(unit(1, 0, 0, 0), [hit("art:51")]) is None


async def test_refusals_and_uncited_answers_are_not_cached(cache: SemanticCache) -> None:
    refused = Answer("Bilmirəm.", (), "m", "stop", refused=True)
    uncited = Answer("Cavab.", (Citation("art:99", "x", "y"),), "m", "stop")
    await cache.store(unit(1, 0, 0, 0), "q", refused, [hit("art:51")])
    await cache.store(unit(1, 0, 0, 0), "q", uncited, [hit("art:51")])  # cites nothing retrieved
    assert await cache.lookup(unit(1, 0, 0, 0), [hit("art:51")]) is None


def test_fingerprint_validity() -> None:
    current = fingerprint([hit("art:51"), hit("art:52", date(2026, 1, 1))])
    assert still_valid({"art:51": "2021-05-27"}, current)
    assert not still_valid({"art:52": "2021-05-27"}, current)
    assert not still_valid({}, current)
