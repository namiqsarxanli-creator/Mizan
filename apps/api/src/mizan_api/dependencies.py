"""Clients for backing services and their readiness checks.

Clients are created lazily (no connection is opened at startup), so the API can boot while a
dependency is still coming up and report it through ``/health/ready`` instead of crashing.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

import asyncpg
import httpx
from neo4j import AsyncDriver, AsyncGraphDatabase
from redis.asyncio import Redis

from mizan_core.config import Settings
from mizan_core.health import DependencyCheck


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

    neo4j = AsyncGraphDatabase.driver(
        settings.neo4j.uri,
        auth=(settings.neo4j.user, settings.neo4j.password.get_secret_value()),
    )
    stack.push_async_callback(neo4j.close)

    return [PostgresCheck(pool), RedisCheck(redis), QdrantCheck(qdrant), Neo4jCheck(neo4j)]
