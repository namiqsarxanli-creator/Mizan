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
