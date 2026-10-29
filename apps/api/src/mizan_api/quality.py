"""Production quality loop: log every answer, judge a sample online, collect user feedback.

    /v1/ask, /v1/agent/ask ─▶ answers (redacted question, answer, sources, cost, variant)
                                  │ 5% sample, after the response is sent
                                  ▼
                            online_evals (faithfulness against the answer's own sources)
    /v1/feedback ─────────▶ feedback (+1 / -1, comment)

    reviews ◀── low confidence, a refusal, a 👎, or an unfaithful online eval (human-in-the-loop)

Online evaluation never delays a response: it runs as a background task with its own small
concurrency limit, and its failures are logged, not raised. Sampling is deterministic in the
answer id, so a replay of the same answers evaluates the same ones.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mizan_core.genai import TRACER, redact
from mizan_core.logging import get_logger
from mizan_ingestion.db.quality import AnswerLog, Feedback, OnlineEval, Review

log = get_logger(__name__)


class Faithfulness(Protocol):
    model: str

    async def judge(
        self, answer: str, sources: Sequence[tuple[str, str]]
    ) -> tuple[float, list[str]]: ...


@dataclass(frozen=True, slots=True)
class AnswerRecord:
    endpoint: str  # "ask" | "agent"
    tenant: str
    question: str
    as_of: date
    answer: str
    sources: Sequence[tuple[str, str]]  # (key, text) the model was given
    model: str | None = None
    prompt_version: str | None = None
    variant: str | None = None
    verdict: str | None = None
    confidence: float | None = None
    tokens: int = 0
    latency_ms: float = 0.0
    trace_id: str | None = None
    abstained: bool = False


def sampled(answer_id: uuid.UUID, rate: float) -> bool:
    """Deterministic per answer: the same id is always in or always out of the sample."""
    return (answer_id.int % 10_000) < rate * 10_000


@dataclass
class QualityLoop:
    sessions: async_sessionmaker[AsyncSession]
    judge: Faithfulness | None
    sample_rate: float = 0.05
    max_concurrent: int = 1
    # Human review: agent answers below this confidence, and sampled answers judged below
    # this faithfulness, go to the review queue (as do refusals and 👎).
    review_below: float = 0.7
    unfaithful_below: float = 0.8
    _pending: set[asyncio.Task[None]] = field(default_factory=set)
    _gate: asyncio.Semaphore | None = None

    async def record(self, r: AnswerRecord) -> uuid.UUID:
        answer_id = uuid.uuid4()
        async with self.sessions.begin() as session:
            session.add(
                AnswerLog(
                    id=answer_id,
                    trace_id=r.trace_id,
                    endpoint=r.endpoint,
                    tenant=r.tenant,
                    question=redact(r.question),
                    as_of=r.as_of,
                    answer=r.answer,
                    sources=[k for k, _ in r.sources],
                    model=r.model,
                    prompt_version=r.prompt_version,
                    variant=r.variant,
                    verdict=r.verdict,
                    confidence=r.confidence,
                    tokens=r.tokens,
                    latency_ms=r.latency_ms,
                )
            )
            await session.flush()
            if r.abstained:
                await self._enqueue(session, answer_id, "abstained")
            elif r.confidence is not None and r.confidence < self.review_below:
                await self._enqueue(session, answer_id, "low_confidence")
        if self.judge is not None and not r.abstained and sampled(answer_id, self.sample_rate):
            task = asyncio.create_task(self._evaluate(answer_id, r))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
        return answer_id

    async def _evaluate(self, answer_id: uuid.UUID, r: AnswerRecord) -> None:
        if self.judge is None:  # pragma: no cover - record() only schedules with a judge
            return
        if self._gate is None:
            self._gate = asyncio.Semaphore(self.max_concurrent)
        async with self._gate:
            with TRACER.start_as_current_span("evaluate faithfulness") as span:
                span.set_attribute("mizan.answer_id", str(answer_id))
                try:
                    score, unsupported = await self.judge.judge(r.answer, r.sources)
                except Exception:  # an evaluation failure must never reach users
                    log.warning("online_eval_failed", answer_id=str(answer_id), exc_info=True)
                    return
                span.set_attribute("mizan.faithfulness", score)
                async with self.sessions.begin() as session:
                    session.add(
                        OnlineEval(
                            answer_id=answer_id,
                            faithfulness=score,
                            unsupported=[redact(u) for u in unsupported],
                            judge_model=self.judge.model,
                        )
                    )
                    if score < self.unfaithful_below:
                        await self._enqueue(session, answer_id, "unfaithful")

    async def feedback(self, answer_id: uuid.UUID, rating: int, comment: str | None) -> bool:
        """Store feedback; False when the answer id is unknown."""
        async with self.sessions.begin() as session:
            exists = await session.scalar(select(AnswerLog.id).where(AnswerLog.id == answer_id))
            if exists is None:
                return False
            session.add(
                Feedback(
                    answer_id=answer_id,
                    rating=rating,
                    comment=redact(comment) if comment else None,
                )
            )
            if rating < 0:
                await session.flush()
                await self._enqueue(session, answer_id, "negative_feedback")
        return True

    @staticmethod
    async def _enqueue(session: AsyncSession, answer_id: uuid.UUID, reason: str) -> None:
        # One review per answer: the first trigger names it; later ones do not reopen it.
        await session.execute(
            insert(Review)
            .values(answer_id=answer_id, reason=reason, status="open")
            .on_conflict_do_nothing(index_elements=["answer_id"])
        )

    async def reviews(self, status: str = "open", limit: int = 50) -> list[dict[str, Any]]:
        """Queued answers with what a reviewer needs to judge them, oldest first."""
        async with self.sessions() as session:
            rows = await session.execute(
                select(Review, AnswerLog)
                .join(AnswerLog, AnswerLog.id == Review.answer_id)
                .where(Review.status == status)
                .order_by(Review.created_at)
                .limit(limit)
            )
            return [
                {
                    "id": review.id,
                    "answer_id": answer.id,
                    "reason": review.reason,
                    "status": review.status,
                    "created_at": review.created_at,
                    "endpoint": answer.endpoint,
                    "question": answer.question,
                    "as_of": answer.as_of,
                    "answer": answer.answer,
                    "sources": list(answer.sources),
                    "confidence": answer.confidence,
                    "reviewer": review.reviewer,
                    "corrected_answer": review.corrected_answer,
                    "note": review.note,
                }
                for review, answer in rows
            ]

    async def resolve(
        self,
        review_id: int,
        *,
        status: str,
        reviewer: str,
        corrected_answer: str | None = None,
        note: str | None = None,
    ) -> bool:
        """Record a reviewer's decision; False when the review id is unknown."""
        async with self.sessions.begin() as session:
            review = await session.get(Review, review_id)
            if review is None:
                return False
            review.status = status
            review.reviewer = reviewer
            review.corrected_answer = corrected_answer
            review.note = redact(note) if note else None
            review.resolved_at = datetime.now(UTC)
        return True

    async def drain(self) -> None:
        """Wait for background evaluations (shutdown, tests)."""
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
