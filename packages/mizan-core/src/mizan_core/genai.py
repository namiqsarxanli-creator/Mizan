"""Telemetry for LLM work, following the OpenTelemetry GenAI semantic conventions.

    request span (FastAPI)
      └─ invoke_agent orchestrator          gen_ai.operation.name = invoke_agent
           ├─ invoke_agent planner
           │    └─ chat openai/gpt-oss-20b  gen_ai.usage.input_tokens, ..., mizan.cost.usd
           ├─ execute_tool search_law       gen_ai.tool.name
           └─ ...

Standard attribute names (``gen_ai.*``) let any OTel backend (Jaeger, Tempo, ClickHouse via
the collector, Langfuse, ...) understand the spans without a custom schema. Mizan-specific
attributes live under ``mizan.*``: cost estimate, prompt version, run outcome.

Cost is an *estimate* from a configurable price table (USD per million tokens, as published
by the provider). Unknown models get no cost attribute rather than a wrong one.

PII: span attributes never hold the user's text verbatim. ``redact`` masks e-mail
addresses, phone numbers, card numbers and Azerbaijani personal identifiers (FIN, ID card).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

TRACER = trace.get_tracer("mizan")

# USD per 1M tokens (input, output). Groq's listed prices in October 2026; verify on the
# provider's pricing page before using them for billing.
PRICES: dict[str, tuple[float, float]] = {
    "openai/gpt-oss-120b": (0.15, 0.60),
    "openai/gpt-oss-20b": (0.075, 0.30),
}

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_PHONE = re.compile(r"(?:\+?994|0)[\s-]?\(?\d{2}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}")
# FIN: 7 characters of digits and capitals, at least one of each (e.g. "5ARPXK2").
_FIN = re.compile(r"(?<![A-Za-z0-9])(?=[0-9A-Z]*\d)(?=[0-9A-Z]*[A-Z])[0-9A-Z]{7}(?![A-Za-z0-9])")
_ID_CARD = re.compile(r"(?<![A-Za-z0-9])(?:AZE|AA)\s?\d{7,8}(?![0-9])")


def redact(text: str) -> str:
    for pattern, label in (
        (_EMAIL, "[EMAIL]"),
        (_ID_CARD, "[ID]"),
        (_PHONE, "[PHONE]"),
        (_CARD, "[CARD]"),
        (_FIN, "[FIN]"),
    ):
        text = pattern.sub(label, text)
    return text


def prompt_version(*parts: str) -> str:
    """A short, stable id of a prompt template: changes exactly when the text changes."""
    digest = hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()
    return digest[:12]


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    return round((input_tokens * price[0] + output_tokens * price[1]) / 1_000_000, 8)


def _set(span: Span, attributes: dict[str, Any]) -> None:
    for key, value in attributes.items():
        if value is not None:
            span.set_attribute(key, value)


@contextmanager
def agent_span(name: str, **attributes: Any) -> Iterator[Span]:
    with TRACER.start_as_current_span(f"invoke_agent {name}") as span:
        _set(span, {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": name})
        _set(span, attributes)
        yield span


@contextmanager
def tool_span(name: str, **attributes: Any) -> Iterator[Span]:
    with TRACER.start_as_current_span(f"execute_tool {name}") as span:
        _set(span, {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name})
        _set(span, attributes)
        yield span


@contextmanager
def chat_span(
    provider: str,
    model: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    prompt: str | None = None,
) -> Iterator[Span]:
    with TRACER.start_as_current_span(f"chat {model}") as span:
        _set(
            span,
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": provider,
                "gen_ai.request.model": model,
                "gen_ai.request.temperature": temperature,
                "gen_ai.request.max_tokens": max_tokens,
                "mizan.prompt.version": prompt,
            },
        )
        try:
            yield span
        except BaseException as exc:
            span.set_attribute("error.type", type(exc).__name__)
            span.set_status(Status(StatusCode.ERROR))
            raise


def record_usage(
    span: Span,
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    response_model: str | None = None,
    finish_reason: str | None = None,
) -> None:
    _set(
        span,
        {
            "gen_ai.response.model": response_model or model,
            "gen_ai.usage.input_tokens": input_tokens,
            "gen_ai.usage.output_tokens": output_tokens,
            "gen_ai.response.finish_reasons": [finish_reason] if finish_reason else None,
            "mizan.cost.usd": cost_usd(model, input_tokens, output_tokens),
        },
    )
