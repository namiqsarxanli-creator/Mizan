"""Multi-agent research (F5): a full answer, or the same run streamed step by step (SSE).

POST /v1/agent/ask          -> AgentAnswer (JSON)
POST /v1/agent/ask/stream   -> text/event-stream:
    event: step    data: {"step": "research", "message": "Maddələr ... axtarılır..."}
    ...
    event: result  data: <AgentAnswer>
    event: error   data: {"detail": "..."}       (the run failed after streaming began)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import date

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from mizan_agents.orchestrator import Event, Orchestrator
from mizan_agents.schemas import AgentAnswer
from mizan_api.budget import TenantBudget, tenant_of
from mizan_api.routes.legal import today_in_baku
from mizan_core.logging import get_logger

router = APIRouter(prefix="/v1/agent", tags=["agent"])
log = get_logger(__name__)


class AgentRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    as_of: date | None = None


def _agent(request: Request) -> Orchestrator:
    agent: Orchestrator | None = request.app.state.agent
    if agent is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The research agent is disabled: it needs MIZAN_LLM__GROQ_API_KEY and the graph.",
        )
    return agent


async def _check_budget(request: Request) -> tuple[TenantBudget | None, str]:
    budget: TenantBudget | None = getattr(request.app.state, "budget", None)
    tenant = tenant_of(request.headers.get("X-Tenant-Id"))
    if budget is not None:
        await budget.check(tenant)
    return budget, tenant


@router.post("/ask", responses={503: {"description": "Agent not configured"}})
async def ask(request: Request, body: AgentRequest) -> AgentAnswer:
    agent = _agent(request)
    budget, tenant = await _check_budget(request)
    result = await agent.run(body.question, as_of=body.as_of or today_in_baku())
    if budget is not None:
        await budget.charge(tenant, result.usage.get("total", 0))
    return result


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post("/ask/stream", responses={503: {"description": "Agent not configured"}})
async def ask_stream(request: Request, body: AgentRequest) -> StreamingResponse:
    agent = _agent(request)
    budget, tenant = await _check_budget(request)
    on = body.as_of or today_in_baku()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def on_event(event: Event) -> None:
        await queue.put(
            _sse(
                "step", {"step": event.step.value, "message": event.message, **(event.detail or {})}
            )
        )

    async def run() -> None:
        try:
            result = await agent.run(body.question, as_of=on, on_event=on_event)
            if budget is not None:
                await budget.charge(tenant, result.usage.get("total", 0))
            await queue.put(_sse("result", result.model_dump(mode="json")))
        except Exception:
            log.exception("agent_run_failed")
            await queue.put(_sse("error", {"detail": "the research run failed"}))
        finally:
            await queue.put(None)

    async def stream() -> AsyncIterator[str]:
        task = asyncio.create_task(run())
        try:
            while (chunk := await queue.get()) is not None:
                yield chunk
        finally:
            task.cancel()  # the client went away: stop spending tokens

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
