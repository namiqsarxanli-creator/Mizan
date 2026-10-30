"""Engine and session factory."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mizan_core.config import PostgresSettings


def create_engine(settings: PostgresSettings) -> AsyncEngine:
    return create_async_engine(settings.sqlalchemy_url, pool_pre_ping=True)


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


ALL_TENANTS = "*"  # reviewers and analytics: every tenant's rows


async def as_tenant(session: AsyncSession, tenant: str) -> None:
    """Scope the current transaction to one tenant's quality rows (row-level security).

    Switches to ``mizan_app`` for the rest of the transaction: the login role may be a superuser
    (as in local docker), and superusers ignore row-level security. Both settings are LOCAL and
    end with the transaction, so a pooled connection never carries them into the next request.
    """
    await session.execute(text("SET LOCAL ROLE mizan_app"))
    await session.execute(
        text("SELECT set_config('mizan.tenant', :tenant, true)"), {"tenant": tenant}
    )
