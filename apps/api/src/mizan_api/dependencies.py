"""Clients for backing services and their readiness checks.

Clients are created lazily (no connection is opened at startup), so the API can boot while a
dependency is still coming up and report it through ``/health/ready`` instead of crashing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import anthropic
import asyncpg
import groq
import httpx
from neo4j import AsyncDriver, AsyncGraphDatabase
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis

from mizan_core.config import Settings
from mizan_core.health import DependencyCheck
from mizan_core.logging import get_logger
from mizan_graph.search import SERVING_POLICY, GraphSearcher
from mizan_graph.store import Neo4jGraph
from mizan_ingestion.chunking import Strategy
from mizan_retrieval.answer import AnswerBackend, ClaudeAnswerer, GroqAnswerer
from mizan_retrieval.embeddings import MODELS, Embedder, EmbeddingCache
from mizan_retrieval.index import alias_for
from mizan_retrieval.rerank import Reranker
from mizan_retrieval.rewrite import GroqQueryRewriter, QueryRewriter
from mizan_retrieval.search import (
    Hit,
    Mode,
    SearchBackend,
    SearchConfig,
    Searcher,
    expand_to_articles,
)

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PostgresCheck:
    pool: Any  # asyncpg.Pool (asyncpg ships no type hints)
    name: str = "postgres"

    async def check(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.fetchval("SELECT 1")


@dataclass(frozen=True, slots=True)
class RedisCheck:
    client: Redis
    name: str = "redis"

    async def check(self) -> None:
        await self.client.ping()


@dataclass(frozen=True, slots=True)
class QdrantCheck:
    http: httpx.AsyncClient
    name: str = "qdrant"

    async def check(self) -> None:
        response = await self.http.get("/readyz")
        response.raise_for_status()


@dataclass(frozen=True, slots=True)
class Neo4jCheck:
    driver: AsyncDriver
    name: str = "neo4j"

    async def check(self) -> None:
        await self.driver.verify_connectivity()


def _neo4j_driver(settings: Settings) -> AsyncDriver:
    return AsyncGraphDatabase.driver(
        settings.neo4j.uri,
        auth=(settings.neo4j.user, settings.neo4j.password.get_secret_value()),
    )


async def build_dependency_checks(
    settings: Settings, stack: AsyncExitStack
) -> list[DependencyCheck]:
    """Create clients for every backing service; ``stack`` closes them on shutdown."""
    pool = await asyncpg.create_pool(dsn=settings.postgres.dsn, min_size=0, max_size=5)
    stack.push_async_callback(pool.close)

    redis = Redis.from_url(settings.redis.url)
    stack.push_async_callback(redis.aclose)

    qdrant_headers = (
        {"api-key": settings.qdrant.api_key.get_secret_value()} if settings.qdrant.api_key else {}
    )
    qdrant = httpx.AsyncClient(base_url=settings.qdrant.url, headers=qdrant_headers)
    stack.push_async_callback(qdrant.aclose)

    neo4j = _neo4j_driver(settings)
    stack.push_async_callback(neo4j.close)

    return [PostgresCheck(pool), RedisCheck(redis), QdrantCheck(qdrant), Neo4jCheck(neo4j)]


# --------------------------------------------------------------------------- legal services


@dataclass(frozen=True, slots=True)
class ModelLoadedCheck:
    """Not ready until a model has finished loading (startup warmup takes ~30 s on CPU)."""

    model: Embedder | Reranker
    name: str = "embedding_model"

    async def check(self) -> None:
        if not self.model.loaded:
            raise RuntimeError("model is still loading")


@dataclass(frozen=True, slots=True)
class CollectionCheck:
    client: AsyncQdrantClient
    alias: str
    name: str = "search_index"

    async def check(self) -> None:
        if not await self.client.collection_exists(self.alias):
            raise RuntimeError(f"collection {self.alias!r} is missing; run `mizan-retrieval index`")


ArticleExpander = Callable[[Sequence[Hit], int, date], Awaitable[list[Hit]]]


@dataclass(slots=True)
class LegalServices:
    searcher: SearchBackend
    config: SearchConfig
    answerer: AnswerBackend | None
    checks: list[DependencyCheck]
    rewriter: QueryRewriter | None = None
    # Parent-child retrieval: replace part-level hits by their whole articles before answering.
    expand: ArticleExpander | None = None


def search_config(settings: Settings) -> SearchConfig:
    r = settings.retrieval
    return SearchConfig(
        mode=Mode(r.mode),
        dense_model=r.dense_model,
        sparse=r.sparse,
        strategy=Strategy(r.strategy),
        candidates=r.candidates,
        rerank_top=r.rerank_top,
    )


async def _warmup(models: list[Embedder | Reranker]) -> None:
    for model in models:
        try:
            await asyncio.to_thread(model.warmup)
        except Exception:
            log.exception("model_warmup_failed", model=type(model).__name__)


def _build_answerer(settings: Settings, stack: AsyncExitStack) -> AnswerBackend | None:
    llm = settings.llm
    if llm.provider == "groq" and llm.groq_api_key is not None:
        groq_client = groq.AsyncGroq(api_key=llm.groq_api_key.get_secret_value())
        stack.push_async_callback(groq_client.close)
        return GroqAnswerer(
            groq_client,
            model=llm.groq_model,
            reasoning_effort=llm.effort,
            max_context_chars=llm.max_context_chars,
        )
    if llm.provider == "anthropic" and llm.anthropic_api_key is not None:
        claude = anthropic.AsyncAnthropic(api_key=llm.anthropic_api_key.get_secret_value())
        stack.push_async_callback(claude.close)
        return ClaudeAnswerer(claude, model=llm.anthropic_model, effort=llm.effort)
    log.warning("answering_disabled", provider=llm.provider, reason="no API key")
    return None


def _build_rewriter(settings: Settings, stack: AsyncExitStack) -> QueryRewriter | None:
    llm = settings.llm
    if not llm.rewrite_queries or llm.groq_api_key is None:
        return None
    client = groq.AsyncGroq(api_key=llm.groq_api_key.get_secret_value())
    stack.push_async_callback(client.close)
    return GroqQueryRewriter(client, model=llm.rewrite_model)


async def build_legal_services(settings: Settings, stack: AsyncExitStack) -> LegalServices:
    config = search_config(settings)
    api_key = settings.qdrant.api_key.get_secret_value() if settings.qdrant.api_key else None
    qdrant = AsyncQdrantClient(url=settings.qdrant.url, api_key=api_key, timeout=30)
    stack.push_async_callback(qdrant.close)

    cache = EmbeddingCache(Path(settings.retrieval.embeddings_cache))
    stack.callback(cache.close)
    embedder = Embedder(MODELS[config.dense_model], cache)
    reranker = Reranker() if config.rerank_top else None
    checks: list[DependencyCheck] = [
        CollectionCheck(qdrant, alias_for(config.strategy)),
        ModelLoadedCheck(embedder),
    ]
    if reranker is not None:
        checks.append(ModelLoadedCheck(reranker, name="rerank_model"))

    if settings.retrieval.warmup:
        models: list[Embedder | Reranker] = [embedder, *([reranker] if reranker else [])]
        task = asyncio.create_task(_warmup(models))
        stack.callback(task.cancel)

    answerer = _build_answerer(settings, stack)
    rewriter = _build_rewriter(settings, stack)

    async def expand(hits: Sequence[Hit], source_id: int, as_of: date) -> list[Hit]:
        return await expand_to_articles(qdrant, hits, source_id=source_id, as_of=as_of)

    searcher: SearchBackend = Searcher(qdrant, {config.dense_model: embedder}, reranker)
    if settings.retrieval.graph and config.mode is Mode.DENSE:
        driver = _neo4j_driver(settings)
        stack.push_async_callback(driver.close)
        searcher = GraphSearcher(searcher, Neo4jGraph(driver), SERVING_POLICY)
    return LegalServices(searcher, config, answerer, checks, rewriter, expand)
