"""Postgres fixtures shared by the integration tests of every package (see pytest `pythonpath`)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mizan_core.config import PostgresSettings, Settings
from mizan_ingestion.db.session import create_engine, session_factory

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_DATABASE = "mizan_test"


@pytest.fixture(scope="session")
def test_postgres() -> PostgresSettings:
    """A fresh database, migrated with Alembic exactly as production would be."""
    if os.environ.get("MIZAN_INTEGRATION") != "1":
        pytest.skip("set MIZAN_INTEGRATION=1 with the compose stack running")

    base = Settings().postgres

    async def recreate() -> None:
        conn = await asyncpg.connect(base.dsn)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{TEST_DATABASE}"')
        finally:
            await conn.close()

    asyncio.run(recreate())
    env = {**os.environ, "MIZAN_POSTGRES__DATABASE": TEST_DATABASE}
    for args in (["upgrade", "head"], ["check"]):  # `check` fails if models drift from migrations
        subprocess.run(  # noqa: S603
            [sys.executable, "-m", "alembic", *args], check=True, env=env, cwd=REPO_ROOT
        )
    return base.model_copy(update={"database": TEST_DATABASE})


@pytest.fixture
async def sessions(
    test_postgres: PostgresSettings,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(test_postgres)
    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE legal_documents, provision_texts, answers RESTART IDENTITY CASCADE")
        )
    yield session_factory(engine)
    await engine.dispose()
