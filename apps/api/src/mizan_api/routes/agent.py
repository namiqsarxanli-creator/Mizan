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
import time
from collections.abc import AsyncIterator
from datetime import date
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from mizan_agents.orchestrator import Event, Orchestrator
from mizan_agents.schemas import AgentAnswer, Verdict
from mizan_api.budget import TenantBudget, tenant_of
from mizan_api.quality import AnswerRecord, QualityLoop
from mizan_api.routes.legal import today_in_baku
from mizan_core.logging import get_logger
from mizan_core.tracing import current_trace_id

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


class AgentResponse(AgentAnswer):
    # Send it back with POST /v1/feedback; None when the quality loop is off.
    answer_id: UUID | None = None


async def _record(
    request: Request, tenant: str, result: AgentAnswer, latency_ms: float
) -> UUID | None:
    quality: QualityLoop | None = getattr(request.app.state, "quality", None)
    if quality is None:
        return None
    return await quality.record(
        AnswerRecord(
            endpoint="agent",
            tenant=tenant,
            question=result.question,
            as_of=result.as_of,
            answer=result.answer,
            sources=[(e.key, e.text) for e in result.evidence],
            verdict=result.verdict.value,
            confidence=result.confidence,
            tokens=result.usage.get("total", 0),
            latency_ms=latency_ms,
            trace_id=current_trace_id(),
            abstained=result.verdict is Verdict.ABSTAIN,
        )
    )


@router.post("/ask", responses={503: {"description": "Agent not configured"}})
async def ask(request: Request, body: AgentRequest) -> AgentResponse:
    agent = _agent(request)
    budget, tenant = await _check_budget(request)
    started = time.perf_counter()
    result = await agent.run(body.question, as_of=body.as_of or today_in_baku())
    if budget is not None:
        await budget.charge(tenant, result.usage.get("total", 0))
    answer_id = await _record(
        request, tenant, result, round((time.perf_counter() - started) * 1000, 1)
    )
    return AgentResponse(**result.model_dump(), answer_id=answer_id)


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post("/ask/stream", responses={503: {"description": "Agent not configured"}})
async def ask_stream(request: Request, body: AgentRequest) -> StreamingResponse:
    agent = _agent(request)
    budget, tenant = await _check_budget(request)
    on = body.as_of or today_in_baku()
    started = time.perf_counter()
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
            answer_id = await _record(
                request, tenant, result, round((time.perf_counter() - started) * 1000, 1)
            )
            payload = {**result.model_dump(mode="json"), "answer_id": answer_id}
            await queue.put(_sse("result", payload))
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
