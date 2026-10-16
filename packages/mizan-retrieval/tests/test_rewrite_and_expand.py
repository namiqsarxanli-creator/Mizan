from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from qdrant_client import AsyncQdrantClient
from test_search import BagOfWordsEmbedder

from mizan_ingestion.chunking import Strategy
from mizan_retrieval.corpus import OPEN_END, ChunkRecord
from mizan_retrieval.index import build_index
from mizan_retrieval.rewrite import GroqQueryRewriter
from mizan_retrieval.search import Hit, expand_to_articles, merge_article

# ------------------------------------------------------------------------------ rewriting


class FakeCompletions:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        message = SimpleNamespace(content=self.reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def rewriter(reply: str | Exception) -> tuple[GroqQueryRewriter, FakeCompletions]:
    completions = FakeCompletions(reply)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return GroqQueryRewriter(client), completions


async def test_expansion_keeps_the_original_question() -> None:
    r, calls = rewriter('"Əmək müqaviləsinə işəgötürənin təşəbbüsü ilə xitam verilməsi"')

    expanded = await r.expand("Məni işdən qova bilərlər?")

    assert expanded == (
        "Məni işdən qova bilərlər?\nƏmək müqaviləsinə işəgötürənin təşəbbüsü ilə xitam verilməsi"
    )
    assert calls.calls[0]["model"] == "openai/gpt-oss-20b"
    assert calls.calls[0]["reasoning_effort"] == "low"
    assert calls.calls[0]["temperature"] == 0.0


@pytest.mark.parametrize("reply", [RuntimeError("rate limited"), "", "   "])
async def test_failures_fall_back_to_the_original(reply: str | Exception) -> None:
    r, _ = rewriter(reply)
    assert await r.expand("Maaşımı gecikdirə bilərlər?") == "Maaşımı gecikdirə bilərlər?"


async def test_identical_rewrite_is_not_duplicated() -> None:
    r, _ = rewriter("sınaq müddəti")
    assert await r.expand("Sınaq müddəti") == "Sınaq müddəti"


# ------------------------------------------------------------------- parent-child expansion

START = date(2000, 1, 1)


def chunk(key: str, body: str, parent: str = "art:70") -> ChunkRecord:
    text = f"Əmək Məcəlləsi > Maddə 70. Xitam\n{body}"
    return ChunkRecord(46943, Strategy.ARTICLE, key, parent, (key,), text, START, OPEN_END)


def hit(key: str, article: str, score: float = 1.0) -> Hit:
    return Hit(key, article, article, "x\ny", score, START, (key,))


def test_merge_orders_parts_naturally_and_keeps_one_breadcrumb() -> None:
    parts = [
        hit("art:70/part:10", "art:70"),
        hit("art:70/part:3-1", "art:70"),
        hit("art:70/part:3", "art:70"),
    ]
    parts = [
        Hit(p.key, p.article_key, p.parent_key, f"crumb\n{p.key}", 1.0, START, ()) for p in parts
    ]
    assert merge_article(parts) == "crumb\nart:70/part:3\nart:70/part:3-1\nart:70/part:10"


async def test_hits_expand_to_whole_articles_in_rank_order() -> None:
    client = AsyncQdrantClient(location=":memory:")
    records = [
        chunk("art:70/part:2", "2. İkinci hissə."),
        chunk("art:70/part:1", "1. Birinci hissə."),
        ChunkRecord(
            46943,
            Strategy.ARTICLE,
            "art:51",
            "art:51",
            ("art:51",),
            "Əmək Məcəlləsi > Maddə 51. Sınaq\nMətn.",
            START,
            OPEN_END,
        ),
    ]
    await build_index(client, Strategy.ARTICLE, records, [BagOfWordsEmbedder()])

    expanded = await expand_to_articles(
        client,
        [hit("art:51", "art:51", 0.9), hit("art:70/part:2", "art:70", 0.5)],
        source_id=46943,
        as_of=date(2026, 1, 1),
    )
    await client.close()

    assert [h.key for h in expanded] == ["art:51", "art:70"]
    assert expanded[1].text == (
        "Əmək Məcəlləsi > Maddə 70. Xitam\n1. Birinci hissə.\n2. İkinci hissə."
    )
    assert expanded[1].score == 0.5
    assert set(expanded[1].cites) == {"art:70/part:1", "art:70/part:2"}
