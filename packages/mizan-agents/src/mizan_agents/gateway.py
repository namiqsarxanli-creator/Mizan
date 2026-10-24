"""LLM gateway: one place for routing, budgets, retries and failure isolation.

    agent ─ role ─▶ route (model, limits) ─▶ circuit breaker ─▶ retries with backoff + jitter
                                                   │                      │
                                         token budget per run      JSON → Pydantic schema
                                                                   (one repair attempt)

* **Routing.** Small, fast models plan and verify; the strongest drafts. On Groq's free tier
  each model has its own tokens-per-minute budget, so routing also spreads load.
* **Retries.** Only transient failures are retried (429, 5xx, timeouts, connection errors),
  with capped exponential backoff and jitter; a provider's ``retry-after`` wins. A 400 or 413
  is a bug or an oversized request and fails at once.
* **Circuit breaker.** After repeated failures a model is skipped for a cool-down instead of
  making every request wait for its timeout.
* **Budget.** A run cannot spend more than its token budget: an agent loop that does not
  converge stops, it does not drain the account.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from mizan_core.logging import get_logger

log = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)


class Role(StrEnum):
    PLANNER = "planner"
    DRAFTER = "drafter"
    VERIFIER = "verifier"


@dataclass(frozen=True, slots=True)
class Route:
    model: str
    max_completion_tokens: int = 1024
    reasoning_effort: str | None = "low"
    temperature: float = 0.0


DEFAULT_ROUTES = {
    Role.PLANNER: Route("openai/gpt-oss-20b", max_completion_tokens=1024),
    Role.DRAFTER: Route(
        "openai/gpt-oss-120b", max_completion_tokens=2048, reasoning_effort="medium"
    ),
    Role.VERIFIER: Route("openai/gpt-oss-20b", max_completion_tokens=1536),
}


class GatewayError(RuntimeError):
    """The request cannot succeed (bad request, unparseable output, retries exhausted)."""


class BudgetExceededError(GatewayError):
    pass


class CircuitOpenError(GatewayError):
    pass


@dataclass(slots=True)
class TokenBudget:
    """Tokens one run may spend, prompt and completion together."""

    limit: int
    used: int = 0
    by_role: Counter[str] = field(default_factory=Counter)

    def reserve(self, tokens: int) -> None:
        if self.used + tokens > self.limit:
            raise BudgetExceededError(f"token budget {self.limit} exhausted ({self.used} used)")

    def charge(self, role: Role, tokens: int) -> None:
        self.used += tokens
        self.by_role[role.value] += tokens


@dataclass(slots=True)
class CircuitBreaker:
    threshold: int = 3
    cooldown_s: float = 30.0
    clock: Callable[[], float] = time.monotonic
    failures: int = 0
    opened_at: float | None = None

    def allow(self) -> bool:
        # Closed, or half-open after the cool-down: let a request probe the model.
        return self.opened_at is None or self.clock() - self.opened_at >= self.cooldown_s

    def success(self) -> None:
        self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self.clock()


def _status(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    return int(status) if isinstance(status, int) else None


def _retry_after(exc: BaseException) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    value = str(headers.get("retry-after", ""))
    try:
        return float(value)
    except ValueError:
        return None


def is_transient(exc: BaseException) -> bool:
    status = _status(exc)
    if status is not None:
        return status == 429 or status >= 500
    return isinstance(exc, TimeoutError | ConnectionError) or "Connection" in type(exc).__name__


class LLMGateway:
    def __init__(
        self,
        client: Any,
        routes: dict[Role, Route] | None = None,
        *,
        max_attempts: int = 4,
        base_delay_s: float = 1.0,
        max_delay_s: float = 30.0,
        timeout_s: float = 60.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        """``client`` is a ``groq.AsyncGroq`` (typed loosely so tests can fake it)."""
        self._client = client
        self.routes = routes or DEFAULT_ROUTES
        self._max_attempts = max_attempts
        self._base = base_delay_s
        self._max_delay = max_delay_s
        self._timeout = timeout_s
        self._sleep = sleep
        self._jitter = jitter
        self._breakers: dict[str, CircuitBreaker] = {}

    def breaker(self, model: str) -> CircuitBreaker:
        return self._breakers.setdefault(model, CircuitBreaker())

    def _delay(self, attempt: int, exc: BaseException) -> float:
        hinted = _retry_after(exc)
        if hinted is not None:
            return min(hinted, self._max_delay)
        backoff = min(self._max_delay, self._base * 2.0**attempt)
        return backoff * (0.5 + self._jitter() / 2)  # "equal jitter": never zero, never herded

    async def _complete(self, route: Route, messages: list[dict[str, str]]) -> Any:
        extra = {"reasoning_effort": route.reasoning_effort} if route.reasoning_effort else {}
        if "gpt-oss" not in route.model:
            extra = {}  # only gpt-oss exposes reasoning effort on Groq
        return await asyncio.wait_for(
            self._client.chat.completions.create(
                model=route.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=route.temperature,
                max_completion_tokens=route.max_completion_tokens,
                **extra,
            ),
            timeout=self._timeout,
        )

    async def structured(
        self, role: Role, *, system: str, user: str, schema: type[T], budget: TokenBudget
    ) -> T:
        """Ask ``role``'s model for JSON and validate it as ``schema``."""
        route = self.routes[role]
        breaker = self.breaker(route.model)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        repaired = False
        attempt = 0
        while True:
            if not breaker.allow():
                raise CircuitOpenError(f"{route.model} is failing; circuit open")
            # ~3 characters per token for Azerbaijani with these tokenizers: a safe upper bound.
            prompt_tokens = sum(len(m["content"]) for m in messages) // 3
            budget.reserve(prompt_tokens + route.max_completion_tokens)
            try:
                response = await self._complete(route, messages)
            except Exception as exc:
                breaker.failure()
                attempt += 1
                if not is_transient(exc) or attempt >= self._max_attempts:
                    raise GatewayError(f"{role.value} call failed: {exc!r}") from exc
                delay = self._delay(attempt - 1, exc)
                log.warning("llm_retry", role=role.value, model=route.model, delay_s=delay)
                await self._sleep(delay)
                continue
            breaker.success()
            usage = getattr(response, "usage", None)
            budget.charge(
                role,
                int(getattr(usage, "prompt_tokens", 0) or 0)
                + int(getattr(usage, "completion_tokens", 0) or 0),
            )
            content = response.choices[0].message.content or ""
            try:
                return schema.model_validate(json.loads(content))
            except (json.JSONDecodeError, ValidationError) as exc:
                if repaired:
                    raise GatewayError(f"{role.value} returned invalid {schema.__name__}") from exc
                # One repair turn: show the model its output and the validation error.
                repaired = True
                messages = [
                    *messages,
                    {"role": "assistant", "content": content[:4000]},
                    {
                        "role": "user",
                        "content": f"Bu cavab sxemə uyğun deyil: {str(exc)[:600]}. "
                        "Yalnız düzgün JSON qaytar.",
                    },
                ]
