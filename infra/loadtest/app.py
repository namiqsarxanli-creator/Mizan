"""The API as deployed, with the LLM replaced by a stub of fixed latency (F11 load test).

Everything Mizan runs itself is real:

- the bge-m3 query embedding on CPU;
- Qdrant search and article expansion;
- the injection screen;
- the Redis rate limiter, budget and audit queue;
- the Postgres quality log.

Only the LLM provider is replaced. Groq's free tier allows 8k tokens a minute, so 100
concurrent users would measure the provider's rate limit, not this service. The stub sleeps
``MIZAN_LOADTEST_LLM_MS`` (default 1600 ms, about the median of the real answerer in F2) with
±25% jitter, then cites the first source verbatim. The ``/v1/ask`` latency reported is
therefore this service's overhead plus a modelled provider call.

    uvicorn infra.loadtest.app:create --factory --port 18090
"""

from __future__ import annotations

import asyncio
import os
import random
from collections.abc import Sequence
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import date

from fastapi import FastAPI

import mizan_api.main as api_main
from mizan_api.dependencies import LegalServices, build_legal_services
from mizan_core.config import Settings, get_settings
from mizan_retrieval.answer import Answer, Citation, source_title
from mizan_retrieval.search import Hit

LLM_MS = float(os.environ.get("MIZAN_LOADTEST_LLM_MS", "1600"))


class StubAnswerer:
    """Answers after a modelled provider latency, citing the top source."""

    async def answer(self, question: str, hits: Sequence[Hit], *, as_of: date) -> Answer:
        await asyncio.sleep(LLM_MS / 1000 * random.uniform(0.75, 1.25))  # noqa: S311
        if not hits:
            return Answer("Müvafiq maddə tapılmadı.", (), "stub", "stop", refused=True)
        top = hits[0]
        body = top.text.splitlines()[-1][:200]
        return Answer(
            text=f"{source_title(top)} üzrə: {body}",
            citations=(Citation(top.key, source_title(top), body),),
            model="stub",
            stop_reason="stop",
            # No usage: the daily token budget would otherwise end the test, not the load.
        )


async def _services(settings: Settings, stack: AsyncExitStack) -> LegalServices:
    services = await build_legal_services(settings, stack)
    # No agent and no shadow: their LLM calls would go to the real provider.
    return replace(services, answerer=StubAnswerer(), agent=None, shadow=None)


def create() -> FastAPI:
    # create_app looks the builder up in its module at startup; swap it for this process.
    setattr(api_main, "build_legal_services", _services)  # noqa: B010
    return api_main.create_app(get_settings())
