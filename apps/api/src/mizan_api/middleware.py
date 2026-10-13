"""ASGI middleware."""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mizan_core.tracing import current_trace_id

TRACE_ID_HEADER = "X-Trace-Id"


class TraceIdHeaderMiddleware:
    """Return the request's trace id to the client.

    A user reporting a bad answer can quote this id, and we can open the exact trace
    (every agent step and LLM call) behind it. Must run inside the OpenTelemetry middleware.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_trace_id(message: Message) -> None:
            if message["type"] == "http.response.start" and (trace_id := current_trace_id()):
                MutableHeaders(scope=message).append(TRACE_ID_HEADER, trace_id)
            await send(message)

        await self.app(scope, receive, send_with_trace_id)
