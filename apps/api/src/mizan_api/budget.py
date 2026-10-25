"""Daily LLM token budget per tenant.

Each tenant has a daily token allowance, counted in Redis under one key per tenant per day
(Baku time) that expires after two days. A request is refused with 429 once the tenant has spent
its allowance; ``Retry-After`` points to the next midnight in Baku. The check runs before the
work and the charge after it, so one request can overshoot by at most its own cost. The per-run
token budget of the agent gateway bounds that cost.

Tenants come from the ``X-Tenant-Id`` header until real authentication arrives (F9); without it,
requests share the "public" allowance.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

BAKU = ZoneInfo("Asia/Baku")
DEFAULT_TENANT = "public"


class BudgetStore(Protocol):
    async def get(self, key: str) -> Any: ...
    async def incrby(self, key: str, amount: int) -> int: ...
    async def expire(self, key: str, seconds: int) -> Any: ...


class BudgetExhaustedError(RuntimeError):
    def __init__(self, tenant: str, retry_after_s: int) -> None:
        super().__init__(f"daily token budget of tenant {tenant!r} is spent")
        self.retry_after_s = retry_after_s


def tenant_of(header: str | None) -> str:
    tenant = (header or "").strip()
    # Tenant ids become Redis key parts: keep them short and boring.
    if not tenant or len(tenant) > 64 or not tenant.replace("-", "").replace("_", "").isalnum():
        return DEFAULT_TENANT
    return tenant


class TenantBudget:
    def __init__(self, store: BudgetStore, daily_tokens: int, *, now: Any = None) -> None:
        self._store = store
        self.daily_tokens = daily_tokens
        self._now = now or (lambda: datetime.now(BAKU))

    def _key(self, tenant: str) -> str:
        return f"mizan:budget:{tenant}:{self._now().date().isoformat()}"

    def _until_midnight(self) -> int:
        now = self._now()
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(int((midnight - now).total_seconds()), 1)

    async def spent(self, tenant: str) -> int:
        value = await self._store.get(self._key(tenant))
        return int(value or 0)

    async def check(self, tenant: str) -> None:
        if await self.spent(tenant) >= self.daily_tokens:
            raise BudgetExhaustedError(tenant, self._until_midnight())

    async def charge(self, tenant: str, tokens: int) -> int:
        if tokens <= 0:
            return await self.spent(tenant)
        key = self._key(tenant)
        total = await self._store.incrby(key, tokens)
        await self._store.expire(key, 2 * 24 * 3600)
        return total
