"""Quality data from production (F6): what was answered, what users said, what the judge found.

answers         one row per /v1/ask or agent answer: redacted question, answer, sources, cost
feedback        thumbs up/down (+ comment) on an answer, from the user
online_evals    faithfulness of a sampled answer against its own sources (LLM judge)
reviews         answers queued for a person (low confidence, refusals, 👎, unfaithful)
shadow_runs     a candidate model or prompt answering the same request in the background
audit_log       who called which endpoint, with what outcome (F10)

These rows are the data flywheel: drift detection reads online_evals, and F7/F8 fine-tuning
reads answers with feedback. Questions are stored redacted (``mizan_core.genai.redact``).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mizan_ingestion.db.schema import Base


class AnswerLog(Base):
    __tablename__ = "answers"
    __table_args__ = (Index("ix_answers_created_at", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    trace_id: Mapped[str | None] = mapped_column(String(32))
    endpoint: Mapped[str] = mapped_column(String(32))  # "ask" | "agent"
    tenant: Mapped[str] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(Text)  # redacted
    as_of: Mapped[date]
    answer: Mapped[str] = mapped_column(Text)
    sources: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    variant: Mapped[str | None] = mapped_column(String(16))  # A/B arm
    verdict: Mapped[str | None] = mapped_column(String(32))  # agent: answer / abstain
    confidence: Mapped[float | None] = mapped_column(Float)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    answer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("answers.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rating: Mapped[int] = mapped_column(SmallInteger)  # +1 / -1
    comment: Mapped[str | None] = mapped_column(Text)  # redacted


class OnlineEval(Base):
    __tablename__ = "online_evals"
    __table_args__ = (Index("ix_online_evals_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    answer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("answers.id", ondelete="CASCADE"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    faithfulness: Mapped[float] = mapped_column(Float)
    unsupported: Mapped[list[str]] = mapped_column(JSONB, default=list)
    judge_model: Mapped[str] = mapped_column(String(128))


class Review(Base):
    """Human-in-the-loop queue (F9): answers a person should look at, and what they decided.

    One row per answer, whatever triggered it first. A correction is training data for the
    verifier and the drafter, and evidence for the eval sets.
    """

    __tablename__ = "reviews"
    __table_args__ = (Index("ix_reviews_status_created_at", "status", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    answer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("answers.id", ondelete="CASCADE"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # low_confidence | abstained | negative_feedback | unfaithful
    reason: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(
        String(16), default="open"
    )  # open | approved | corrected | rejected
    reviewer: Mapped[str | None] = mapped_column(String(64))
    corrected_answer: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ShadowRun(Base):
    """A candidate model or prompt answering the same request in the background (F9).

    The user only ever sees the primary answer. Comparing the two on real traffic, before a
    switch, is what an offline eval set cannot do: real questions, real retrieval, real latency.
    """

    __tablename__ = "shadow_runs"
    __table_args__ = (Index("ix_shadow_runs_candidate_created_at", "candidate", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    answer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("answers.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    candidate: Mapped[str] = mapped_column(String(128))  # model (and prompt version) under test
    answer: Mapped[str] = mapped_column(Text)
    refused: Mapped[bool]
    citations: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    primary_tokens: Mapped[int] = mapped_column(Integer, default=0)
    primary_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    # Jaccard overlap of cited provisions, primary vs candidate; 1.0 when both cite nothing.
    citation_overlap: Mapped[float] = mapped_column(Float)
    refusal_agrees: Mapped[bool]
    # Set when the candidate failed (provider error, invalid JSON): the other fields are empty.
    # A candidate that fails where the primary answers is a result, not noise.
    error: Mapped[str | None] = mapped_column(Text)


class AuditEvent(Base):
    """Who called what (F10): one row per authenticated or refused /v1 request.

    Append-only: the API's role may insert but not change or delete rows. Bodies are not
    stored (questions are in ``answers``, redacted); the trace id links the two.
    """

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_tenant_at", "tenant", "at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    subject: Mapped[str | None] = mapped_column(String(128))  # None: no valid credentials
    tenant: Mapped[str | None] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(256))
    status: Mapped[int] = mapped_column(SmallInteger)
    # ok | missing_token | invalid_token | forbidden | rate_limited
    outcome: Mapped[str] = mapped_column(String(16))
    trace_id: Mapped[str | None] = mapped_column(String(32))
    latency_ms: Mapped[float] = mapped_column(Float)
