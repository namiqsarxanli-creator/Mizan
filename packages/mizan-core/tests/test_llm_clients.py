from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from mizan_core.config import LLMSettings
from mizan_core.llm_clients import FailoverClient, failover_chain


class ProviderFailedError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class Provider:
    def __init__(self, name: str, fail: int | None = None) -> None:
        self.name = name
        self.fail = fail
        self.models: list[str] = []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **request: Any) -> Any:
        self.models.append(request["model"])
        if self.fail is not None:
            raise ProviderFailedError(self.fail)
        return SimpleNamespace(provider=self.name)


async def test_rate_limit_moves_to_the_next_provider_and_stays() -> None:
    groq, other = Provider("groq", fail=429), Provider("openrouter")
    client = FailoverClient([(groq, "openai/gpt-oss-120b"), (other, "gpt-oss-120b")])
    assert (await client.create(model="x", messages=[])).provider == "openrouter"
    assert (await client.create(model="x", messages=[])).provider == "openrouter"
    # Each provider got its own name for the model; the exhausted one was tried once.
    assert groq.models == ["openai/gpt-oss-120b"]
    assert other.models == ["gpt-oss-120b", "gpt-oss-120b"]
    assert client.model == "gpt-oss-120b"


async def test_a_bad_request_is_not_retried_elsewhere() -> None:
    first, second = Provider("a", fail=400), Provider("b")
    client = FailoverClient([(first, "m"), (second, "m")])
    with pytest.raises(ProviderFailedError):
        await client.create(model="m", messages=[])
    assert second.models == []


async def test_when_every_provider_fails_the_last_error_is_raised() -> None:
    client = FailoverClient([(Provider("a", fail=429), "m"), (Provider("b", fail=402), "m")])
    with pytest.raises(ProviderFailedError, match="402"):
        await client.create(model="m", messages=[])


def test_chain_skips_providers_without_a_key() -> None:
    settings = LLMSettings(openrouter_api_key=SecretStr("k"))
    chain = failover_chain(["cerebras:gpt-oss-120b", "openrouter:openai/gpt-oss-120b"], settings)
    assert [(c.provider, m) for c, m in chain] == [("openrouter", "openai/gpt-oss-120b")]
