"""Test doubles for the API's search and answer services."""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from datetime import date
from types import SimpleNamespace
from typing import Any

from mizan_api.dependencies import LegalServices
from mizan_ingestion.diff import Amendment, ArticleDiff, ChangeKind, ProvisionChange
from mizan_retrieval.answer import GroqAnswerer
from mizan_retrieval.search import Hit, SearchConfig

HIT = Hit(
    key="art:69/part:1",
    article_key="art:69",
    parent_key="art:69",
    text="Əmək Məcəlləsi > Maddə 69. Xitam\n1. İşçi bir təqvim ayı qabaqcadan xəbərdar edir.",
    score=0.03,
    valid_from=date(2025, 6, 13),
    cites=("art:69/part:1",),
)


class FakeSearcher:
    def __init__(self, hits: list[Hit] | None = None) -> None:
        self.hits = [HIT] if hits is None else hits
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append({"query": query, "as_of": as_of, "k": k, "source_id": source_id})
        return self.hits[:k]


class FakeGroq:
    """Mimics ``AsyncGroq().chat.completions.create`` with a fixed JSON answer."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **_: Any) -> Any:
        content = json.dumps(
            {
                "answer": "İşəgötürəni bir ay əvvəl xəbərdar etməlisiniz.",
                "citations": [
                    {"source": 1, "quote": "bir təqvim ayı qabaqcadan"},
                    {"source": 1, "quote": "iki həftə əvvəl"},  # not in the source
                ],
            },
            ensure_ascii=False,
        )
        message = SimpleNamespace(content=content)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            model="openai/gpt-oss-120b",
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )


class PrefixRewriter:
    async def expand(self, question: str) -> str:
        return f"{question}\nhüquqi ifadə"


async def expand_to_fake_articles(hits: Sequence[Hit], source_id: int, as_of: date) -> list[Hit]:
    return [
        Hit(h.article_key, h.article_key, h.article_key, h.text, h.score, h.valid_from, h.cites)
        for h in hits
    ]


class FakeDiff:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, date, date]] = []

    async def diff(self, source_id: int, key: str, start: date, end: date) -> ArticleDiff | None:
        self.calls.append((source_id, key, start, end))
        if start.year < 1999:  # before the Code existed
            return None
        change = ProvisionChange(
            "art:69/part:1",
            ChangeKind.CHANGED,
            "müqaviləsini ləğv edə",
            "müqaviləsinə xitam verə",
            "[-müqaviləsini-]{+müqaviləsinə+} [-ləğv-]{+xitam+} [-edə-]{+verə+}",
        )
        amendment = Amendment("332-VIIQD", date(2025, 12, 30), "... əvəz edilmişdir.")
        return ArticleDiff(
            key, start, end, date(2021, 5, 27), date(2026, 7, 14), [change], [amendment]
        )


def fake_legal_services(
    searcher: FakeSearcher | None = None, *, with_llm: bool = False
) -> LegalServices:
    return LegalServices(
        searcher=searcher or FakeSearcher(),
        config=SearchConfig(),
        answerer=GroqAnswerer(FakeGroq()) if with_llm else None,
        checks=[],
        rewriter=PrefixRewriter() if with_llm else None,
        expand=expand_to_fake_articles,
        diff=FakeDiff(),
    )
