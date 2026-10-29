"""Shadow deployment (F9): a candidate answerer runs on a sample of real requests, unseen.

    /v1/ask ──▶ primary answer ──▶ response
                  │ sampled answer ids, after the response is sent
                  ▼
            candidate answers the same question with the same sources ──▶ shadow_runs

The candidate gets exactly the primary's retrieved sources, so the comparison isolates the
model or prompt. Shadow calls are platform cost: they are not charged to the tenant's budget,
they never delay a response, and their failures are logged, not raised. ``mizan-eval shadow``
turns the rows into the comparison report.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mizan_api.quality import sampled
from mizan_core.genai import TRACER
from mizan_core.logging import get_logger
from mizan_ingestion.db.quality import ShadowRun
from mizan_retrieval.answer import Answer, AnswerBackend
from mizan_retrieval.search import Hit

log = get_logger(__name__)


def citation_overlap(a: Answer, b: Answer) -> float:
    """Jaccard overlap of the cited provisions; 1.0 when neither cites anything."""
    x, y = {c.key for c in a.citations}, {c.key for c in b.citations}
    return len(x & y) / len(x | y) if x | y else 1.0


@dataclass
class ShadowRunner:
    sessions: async_sessionmaker[AsyncSession]
    candidate: AnswerBackend
    label: str  # what is being tested, e.g. "openai/gpt-oss-20b"
    sample_rate: float = 0.1
    max_concurrent: int = 1
    _pending: set[asyncio.Task[None]] = field(default_factory=set)
    _gate: asyncio.Semaphore | None = None

    def submit(
        self,
        answer_id: uuid.UUID,
        question: str,
        hits: Sequence[Hit],
        as_of: date,
        primary: Answer,
        primary_latency_ms: float,
    ) -> bool:
        """Schedule the candidate for this request if it is in the sample; True if scheduled."""
        # A different slice of the id space than the online-eval sample, so the two overlap
        # only by chance.
        if not sampled(uuid.UUID(int=answer_id.int ^ 0x5A5A), self.sample_rate):
            return False
        task = asyncio.create_task(
            self._run(answer_id, question, list(hits), as_of, primary, primary_latency_ms)
        )
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return True

    async def _run(
        self,
        answer_id: uuid.UUID,
        question: str,
        hits: list[Hit],
        as_of: date,
        primary: Answer,
        primary_latency_ms: float,
    ) -> None:
        if self._gate is None:
            self._gate = asyncio.Semaphore(self.max_concurrent)
        async with self._gate:
            with TRACER.start_as_current_span("shadow answer") as span:
                span.set_attribute("mizan.answer_id", str(answer_id))
                span.set_attribute("mizan.shadow.candidate", self.label)
                started = time.perf_counter()
                common = {
                    "answer_id": answer_id,
                    "candidate": self.label,
                    "primary_tokens": sum(primary.usage.values()),
                    "primary_latency_ms": primary_latency_ms,
                }
                try:
                    shadow = await self.candidate.answer(question, hits, as_of=as_of)
                except Exception as exc:  # a shadow failure must never reach users
                    log.warning("shadow_failed", answer_id=str(answer_id), exc_info=True)
                    # ...but it is recorded: the candidate failing where the primary answered
                    # is exactly what a shadow test is for.
                    row = ShadowRun(
                        **common,
                        answer="",
                        refused=False,
                        citations=[],
                        tokens=0,
                        latency_ms=round((time.perf_counter() - started) * 1000, 1),
                        citation_overlap=0.0,
                        refusal_agrees=False,
                        error=f"{type(exc).__name__}: {exc}"[:500],
                    )
                else:
                    row = ShadowRun(
                        **common,
                        answer=shadow.text,
                        refused=shadow.refused,
                        citations=[c.key for c in shadow.citations],
                        tokens=sum(shadow.usage.values()),
                        latency_ms=round((time.perf_counter() - started) * 1000, 1),
                        citation_overlap=citation_overlap(primary, shadow),
                        refusal_agrees=primary.refused == shadow.refused,
                    )
                async with self.sessions.begin() as session:
                    session.add(row)

    async def drain(self) -> None:
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
