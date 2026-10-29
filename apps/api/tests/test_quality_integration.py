"""The quality loop against a real, migrated Postgres."""

import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mizan_api.quality import AnswerRecord, QualityLoop, sampled
from mizan_ingestion.db.quality import AnswerLog, Feedback, OnlineEval

pytestmark = pytest.mark.integration


class Judge:
    model = "fake-judge"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def judge(
        self, answer: str, sources: Sequence[tuple[str, str]]
    ) -> tuple[float, list[str]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("rate limited")
        return 0.5, ["Zəng edin: +994 50 123 45 67"]


RECORD = AnswerRecord(
    endpoint="ask",
    tenant="acme",
    question="Mənim FİN kodum 5ARPXK2, işdən çıxa bilərəmmi?",
    as_of=date(2026, 10, 25),
    answer="Bəli, bir ay əvvəl xəbərdar etməklə.",
    sources=[("art:69", "mətn")],
    model="openai/gpt-oss-120b",
    tokens=1234,
)


async def test_answers_are_logged_redacted_and_sampled_answers_judged(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    judge = Judge()
    loop = QualityLoop(sessions, judge, sample_rate=1.0)

    answer_id = await loop.record(RECORD)
    await loop.drain()

    async with sessions() as session:
        row = await session.get(AnswerLog, answer_id)
        evaluation = await session.scalar(
            select(OnlineEval).where(OnlineEval.answer_id == answer_id)
        )
    assert row is not None
    assert row.question == "Mənim FİN kodum [FIN], işdən çıxa bilərəmmi?"
    assert (row.sources, row.tokens) == (["art:69"], 1234)
    assert evaluation is not None
    assert evaluation.faithfulness == 0.5
    assert evaluation.unsupported == ["Zəng edin: [PHONE]"]


async def test_nothing_is_judged_outside_the_sample_or_for_abstentions(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    judge = Judge()
    await QualityLoop(sessions, judge, sample_rate=0.0).record(RECORD)
    loop = QualityLoop(sessions, judge, sample_rate=1.0)
    await loop.record(replace(RECORD, abstained=True))
    await loop.drain()
    assert judge.calls == 0


async def test_judge_failures_are_swallowed(sessions: async_sessionmaker[AsyncSession]) -> None:
    loop = QualityLoop(sessions, Judge(fail=True), sample_rate=1.0)
    answer_id = await loop.record(RECORD)
    await loop.drain()
    async with sessions() as session:
        assert await session.get(AnswerLog, answer_id) is not None
        assert await session.scalar(select(OnlineEval)) is None


async def test_feedback_needs_a_known_answer(sessions: async_sessionmaker[AsyncSession]) -> None:
    loop = QualityLoop(sessions, None)
    answer_id = await loop.record(RECORD)

    assert await loop.feedback(answer_id, -1, "Yanlışdır, e-poçt: a@b.az")
    assert not await loop.feedback(uuid.uuid4(), 1, None)

    async with sessions() as session:
        (fb,) = (await session.scalars(select(Feedback))).all()
    assert (fb.rating, fb.comment) == (-1, "Yanlışdır, e-poçt: [EMAIL]")


def test_sampling_is_deterministic_and_close_to_the_rate() -> None:
    ids = [uuid.UUID(int=i * 7919) for i in range(10_000)]
    share = sum(sampled(i, 0.05) for i in ids) / len(ids)
    assert 0.04 < share < 0.06
    assert all(sampled(i, 0.05) == sampled(i, 0.05) for i in ids[:100])


async def test_review_queue_triggers_and_decisions(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    loop = QualityLoop(sessions, Judge(), sample_rate=1.0, review_below=0.7, unfaithful_below=0.8)
    confident = await loop.record(replace(RECORD, endpoint="agent", confidence=0.9))
    unsure = await loop.record(replace(RECORD, endpoint="agent", confidence=0.6))
    refused = await loop.record(replace(RECORD, abstained=True))
    await loop.drain()  # the fake judge scores 0.5: every judged answer is also "unfaithful"
    assert await loop.feedback(unsure, -1, None)  # already queued: no second review

    queued = {r["answer_id"]: r for r in await loop.reviews()}
    assert queued[unsure]["reason"] == "low_confidence"
    assert queued[refused]["reason"] == "abstained"
    assert queued[confident]["reason"] == "unfaithful"
    assert len(queued) == 3

    review = queued[unsure]["id"]
    assert await loop.resolve(review, status="corrected", reviewer="namiq", corrected_answer="x")
    assert not await loop.resolve(10_000_000, status="approved", reviewer="namiq")
    assert unsure not in {r["answer_id"] for r in await loop.reviews()}
    (done,) = await loop.reviews("corrected")
    assert (done["reviewer"], done["corrected_answer"]) == ("namiq", "x")
