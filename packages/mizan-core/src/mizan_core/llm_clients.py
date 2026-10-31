"""OpenAI-compatible chat clients for several providers, behind one tiny interface.

Groq, Gemini, Cerebras and OpenRouter all expose ``POST <base>/chat/completions`` in the OpenAI
format. Mizan's LLM code only ever calls ``client.chat.completions.create(**kwargs)`` and reads
``choices[0].message.content``, ``choices[0].finish_reason``, ``usage`` and ``model``. This
client provides exactly that over httpx, so any of these providers can be plugged in.

A provider error is raised as :class:`ProviderError` with ``status_code`` and ``response``,
the attributes the gateway's retry policy inspects (429 and 5xx are transient).

Models are addressed as ``"provider:model"``: ``"gemini:gemini-2.5-flash"``,
``"cerebras:gpt-oss-120b"``. A bare model name means Groq, the default provider.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx

from mizan_core.config import LLMSettings

BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "cerebras": "https://api.cerebras.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


class ProviderError(RuntimeError):
    def __init__(self, provider: str, response: httpx.Response) -> None:
        super().__init__(f"{provider} HTTP {response.status_code}: {response.text[:300]}")
        self.status_code = response.status_code
        self.response = response


def _ns(value: Any) -> Any:
    """JSON -> attribute access, like the official SDKs' response objects."""
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_ns(v) for v in value]
    return value


class OpenAICompatible:
    def __init__(self, provider: str, api_key: str, *, timeout_s: float = 120.0) -> None:
        self.provider = provider
        self._http = httpx.AsyncClient(
            base_url=BASE_URLS[provider],
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **request: Any) -> Any:
        body = dict(request)
        model = str(body.get("model", ""))
        if self.provider == "gemini":
            # Gemini's compatibility layer takes max_tokens. Its "thinking" models spend the
            # completion budget on reasoning unless told otherwise, so reasoning_effort passes
            # through for Gemini models (e.g. "none") and is dropped for Gemma.
            if "max_completion_tokens" in body:
                body["max_tokens"] = body.pop("max_completion_tokens")
            if not model.startswith("gemini"):
                body.pop("reasoning_effort", None)
        elif "gpt-oss" not in model:
            body.pop("reasoning_effort", None)
        response = await self._http.post("/chat/completions", json=body)
        if response.status_code >= 400:
            raise ProviderError(self.provider, response)
        return _ns(response.json())

    async def close(self) -> None:
        await self._http.aclose()


def split_model(spec: str) -> tuple[str, str]:
    """``"gemini:gemini-2.5-flash"`` -> ("gemini", "gemini-2.5-flash"); bare names are Groq."""
    provider, sep, model = spec.partition(":")
    if sep and provider in BASE_URLS:
        return provider, model
    return "groq", spec


def client_for(provider: str, settings: LLMSettings) -> OpenAICompatible:
    keys = {
        "groq": settings.groq_api_key,
        "gemini": settings.gemini_api_key,
        "cerebras": settings.cerebras_api_key,
        "openrouter": settings.openrouter_api_key,
    }
    key = keys.get(provider)
    if key is None:
        raise ValueError(f"no API key for {provider}: set MIZAN_LLM__{provider.upper()}_API_KEY")
    return OpenAICompatible(provider, key.get_secret_value())


# Status codes worth trying the next provider for: quota, rate limits, provider failures.
FAILOVER_STATUS = frozenset({402, 408, 409, 429, 500, 502, 503, 504})


class FailoverClient:
    """One chat client over several providers serving the same model, tried in order.

    Free tiers cap tokens per day per provider. When the current provider answers with a rate
    limit or a server error, the request goes to the next one, and the client stays there: a
    daily limit does not lift for hours, so retrying the exhausted provider first wastes a
    call. Each entry pairs a client with the model name *that provider* uses (Groq's
    ``openai/gpt-oss-120b`` is Cerebras' ``gpt-oss-120b``). Only providers serving the same
    weights belong in one chain; a different model is a different system, not a fallback.
    """

    def __init__(self, chain: list[tuple[Any, str]]) -> None:
        if not chain:
            raise ValueError("an empty provider chain")
        self._chain = chain
        self._current = 0
        self.chat = SimpleNamespace(completions=self)

    @property
    def model(self) -> str:
        return self._chain[self._current][1]

    async def create(self, **request: Any) -> Any:
        last: Exception | None = None
        for index in range(self._current, len(self._chain)):
            client, model = self._chain[index]
            try:
                response = await client.chat.completions.create(**{**request, "model": model})
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if status is not None and status not in FAILOVER_STATUS:
                    raise  # a bad request fails the same way everywhere
                last = exc
                continue
            self._current = index
            return response
        raise last if last is not None else RuntimeError("no provider answered")


def failover_chain(specs: list[str], settings: LLMSettings) -> list[tuple[OpenAICompatible, str]]:
    """Clients for the specs whose provider has a key, e.g. ``"cerebras:gpt-oss-120b"``."""
    chain = []
    for spec in specs:
        provider, model = split_model(spec)
        try:
            chain.append((client_for(provider, settings), model))
        except ValueError:
            continue  # no key for this provider: skip it
    return chain
