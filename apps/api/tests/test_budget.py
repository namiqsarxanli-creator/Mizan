"""Daily token budget per tenant."""

from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest
from fakes import fake_legal_services
from fastapi.testclient import TestClient

from mizan_api.budget import BAKU, BudgetExhaustedError, TenantBudget, tenant_of
from mizan_api.main import create_app
from mizan_core.config import Environment, Settings


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttl: dict[str, int] = {}

    async def get(self, key: str) -> Any:
        return self.values.get(key)

    async def incrby(self, key: str, amount: int) -> int:
        self.values[key] = self.values.get(key, 0) + amount
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttl[key] = seconds
        return True


NOW = datetime(2026, 10, 25, 23, 0, tzinfo=BAKU)


async def test_spending_is_counted_per_tenant_per_day() -> None:
    redis = FakeRedis()
    budget = TenantBudget(redis, 1000, now=lambda: NOW)

    await budget.charge("acme", 600)
    await budget.charge("acme", 300)
    await budget.charge("other", 50)

    assert await budget.spent("acme") == 900
    assert redis.ttl["mizan:budget:acme:2026-10-25"] == 2 * 24 * 3600
    await budget.check("acme")  # 900 < 1000: allowed


async def test_a_spent_budget_is_refused_until_midnight_in_baku() -> None:
    budget = TenantBudget(FakeRedis(), 1000, now=lambda: NOW)
    await budget.charge("acme", 1000)
    with pytest.raises(BudgetExhaustedError) as caught:
        await budget.check("acme")
    assert caught.value.retry_after_s == 3600


@pytest.mark.parametrize(
    ("header", "tenant"),
    [
        (None, "public"),
        ("", "public"),
        ("acme-1", "acme-1"),
        ("a:b", "public"),
        ("x" * 65, "public"),
    ],
)
def test_tenant_ids_are_sanitised(header: str | None, tenant: str) -> None:
    assert tenant_of(header) == tenant


def test_ask_is_refused_with_429_when_the_budget_is_spent() -> None:
    redis = FakeRedis()
    budget = TenantBudget(redis, 10, now=lambda: NOW)
    redis.values["mizan:budget:acme:2026-10-25"] = 10
    services = replace(fake_legal_services(with_llm=True), budget=budget)
    settings = Settings(_env_file=None, environment=Environment.TEST)
    with TestClient(create_app(settings, checks=[], legal=services)) as client:
        refused = client.post(
            "/v1/ask", json={"question": "İşdən çıxmaq?"}, headers={"X-Tenant-Id": "acme"}
        )
        allowed = client.post(
            "/v1/ask", json={"question": "İşdən çıxmaq?"}, headers={"X-Tenant-Id": "other"}
        )
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "3600"
    assert allowed.status_code == 200
    assert redis.values["mizan:budget:other:2026-10-25"] == 2  # the fake answer's usage
