"""Gateway: routing, retries with backoff, circuit breaker, budget, schema repair."""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from mizan_agents.gateway import (
    BudgetExceededError,
    CircuitBreaker,
    CircuitOpenError,
    GatewayError,
    LLMGateway,
    Role,
    TokenBudget,
)
from mizan_agents.schemas import Plan


class ProviderError(Exception):
    def __init__(self, status: int, retry_after: str | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status
        headers = {"retry-after": retry_after} if retry_after else {}
        self.response = SimpleNamespace(headers=headers)


def reply(payload: object, tokens: int = 100) -> Any:
    content = payload if isinstance(payload, str) else json.dumps(payload)
    message = SimpleNamespace(content=content)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=tokens, completion_tokens=tokens),
    )


class ScriptedLLM:
    """Returns (or raises) the scripted outcomes in order; records the requests."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **request: Any) -> Any:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


PLAN = {"sub_questions": [{"text": "Sınaq müddəti nə qədərdir?", "intent": "rule"}]}


def gateway(llm: ScriptedLLM, sleeps: list[float] | None = None, **kwargs: Any) -> LLMGateway:
    async def sleep(seconds: float) -> None:
        (sleeps if sleeps is not None else []).append(seconds)

    return LLMGateway(llm, sleep=sleep, jitter=lambda: 1.0, **kwargs)


async def ask(gw: LLMGateway, budget: TokenBudget | None = None) -> Plan:
    return await gw.structured(
        Role.PLANNER, system="s", user="u", schema=Plan, budget=budget or TokenBudget(100_000)
    )


async def test_routes_by_role_and_validates_the_schema() -> None:
    llm = ScriptedLLM(reply(PLAN))
    plan = await ask(gateway(llm))
    assert plan.sub_questions[0].text == "Sınaq müddəti nə qədərdir?"
    assert llm.requests[0]["model"] == "openai/gpt-oss-20b"
    assert llm.requests[0]["response_format"] == {"type": "json_object"}


async def test_transient_errors_are_retried_with_exponential_backoff() -> None:
    sleeps: list[float] = []
    llm = ScriptedLLM(ProviderError(503), ProviderError(429), reply(PLAN))
    await ask(gateway(llm, sleeps, base_delay_s=1.0))
    assert sleeps == [1.0, 2.0]  # jitter fixed at its maximum


async def test_retry_after_header_wins() -> None:
    sleeps: list[float] = []
    llm = ScriptedLLM(ProviderError(429, retry_after="7"), reply(PLAN))
    await ask(gateway(llm, sleeps))
    assert sleeps == [7.0]


async def test_bad_requests_fail_at_once() -> None:
    llm = ScriptedLLM(ProviderError(413), reply(PLAN))
    with pytest.raises(GatewayError, match="planner call failed"):
        await ask(gateway(llm))
    assert len(llm.requests) == 1


async def test_retries_are_bounded() -> None:
    llm = ScriptedLLM(*(ProviderError(500) for _ in range(5)))
    with pytest.raises(GatewayError):
        await ask(gateway(llm, max_attempts=3))
    assert len(llm.requests) == 3


async def test_invalid_output_gets_one_repair_turn() -> None:
    llm = ScriptedLLM(reply({"sub_questions": []}), reply(PLAN))
    plan = await ask(gateway(llm))
    assert len(plan.sub_questions) == 1
    repair = llm.requests[1]["messages"]
    assert repair[-2]["role"] == "assistant"
    assert "sxemə uyğun deyil" in repair[-1]["content"]


async def test_provider_json_validation_errors_get_one_repair_turn() -> None:
    invalid = ProviderError(400)
    invalid.args = ("Error code: 400 - {'code': 'json_validate_failed'}",)
    llm = ScriptedLLM(invalid, reply(PLAN))
    plan = await ask(gateway(llm))
    assert len(plan.sub_questions) == 1
    assert "etibarlı JSON deyildi" in llm.requests[1]["messages"][-1]["content"]

    twice = ScriptedLLM(invalid, invalid)
    with pytest.raises(GatewayError):
        await ask(gateway(twice))


async def test_still_invalid_after_repair_fails() -> None:
    llm = ScriptedLLM(reply("not json"), reply("still not json"))
    with pytest.raises(GatewayError, match="invalid Plan"):
        await ask(gateway(llm))


async def test_budget_is_charged_and_enforced() -> None:
    budget = TokenBudget(5_000)
    await ask(gateway(ScriptedLLM(reply(PLAN, tokens=1_000))), budget)
    assert (budget.used, budget.by_role["planner"]) == (2_000, 2_000)

    with pytest.raises(BudgetExceededError):
        await ask(gateway(ScriptedLLM(reply(PLAN))), TokenBudget(500))


def test_circuit_opens_after_failures_and_half_opens_after_cooldown() -> None:
    now = [0.0]
    breaker = CircuitBreaker(threshold=2, cooldown_s=30, clock=lambda: now[0])
    breaker.failure()
    assert breaker.allow()
    breaker.failure()
    assert not breaker.allow()
    now[0] = 31.0
    assert breaker.allow()
    breaker.success()
    assert breaker.opened_at is None


async def test_open_circuit_fails_fast() -> None:
    llm = ScriptedLLM()
    gw = gateway(llm)
    breaker = gw.breaker("openai/gpt-oss-20b")
    breaker.failure()
    breaker.failure()
    breaker.failure()
    with pytest.raises(CircuitOpenError):
        await ask(gw)
    assert llm.requests == []
