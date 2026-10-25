"""Drift detection, including the F6 DoD scenario: a synthetic quality drop raises an alert."""

import os
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mizan_evals.drift import DailyScore, daily_scores, detect_drift
from mizan_ingestion.db.quality import AnswerLog, OnlineEval

TODAY = date(2026, 10, 25)


def days(mean: float, first: int, last: int, n: int = 10) -> list[DailyScore]:
    """Daily scores from ``first`` to ``last`` days ago."""
    return [DailyScore(TODAY - timedelta(days=d), mean, n) for d in range(first, last + 1)]


def test_stable_quality_is_ok() -> None:
    report = detect_drift(days(0.95, 0, 34), today=TODAY)
    assert report.status == "ok"
    assert not report.alert


def test_a_relative_drop_is_drift_even_above_the_slo() -> None:
    # With faithfulness <= 1, a >10% drop is above the 0.90 SLO only for a lower SLO.
    report = detect_drift(days(0.85, 0, 6) + days(1.0, 7, 34), today=TODAY, slo=0.8)
    assert report.status == "drift"
    assert report.relative_drop == pytest.approx(-0.15)
    assert report.alert


def test_falling_below_the_slo_without_a_drop_is_a_breach() -> None:
    report = detect_drift(days(0.86, 0, 34), today=TODAY)
    assert report.status == "slo_breach"


def test_both() -> None:
    report = detect_drift(days(0.70, 0, 6) + days(0.95, 7, 34), today=TODAY)
    assert report.status == "drift+slo_breach"
    assert report.relative_drop == pytest.approx(0.70 / 0.95 - 1)


def test_too_few_samples_are_not_an_alert() -> None:
    report = detect_drift(days(0.2, 0, 1, n=5), today=TODAY)
    assert report.status == "insufficient_data"
    assert not report.alert


def test_means_are_weighted_by_samples() -> None:
    scores = [DailyScore(TODAY, 1.0, 90), DailyScore(TODAY - timedelta(days=1), 0.0, 10)]
    assert detect_drift(scores, today=TODAY).recent_mean == pytest.approx(0.9)


integration = pytest.mark.skipif(
    os.environ.get("MIZAN_INTEGRATION") != "1",
    reason="set MIZAN_INTEGRATION=1 with the compose stack running",
)


@integration
@pytest.mark.integration
async def test_a_synthetic_quality_drop_in_the_database_raises_an_alert(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """F6 DoD: inject 4 weeks of good online evals, then a bad week; the check must alert."""
    async with sessions.begin() as session:
        for ago in range(0, 35):
            faithfulness = 0.65 if ago < 7 else 0.95
            when = datetime.combine(TODAY - timedelta(days=ago), datetime.min.time(), UTC)
            for _ in range(4):
                answer = AnswerLog(
                    id=uuid.uuid4(),
                    created_at=when,
                    endpoint="ask",
                    tenant="synthetic",
                    question="q",
                    as_of=TODAY,
                    answer="a",
                )
                session.add(answer)
                await session.flush()
                session.add(
                    OnlineEval(
                        answer_id=answer.id,
                        created_at=when,
                        faithfulness=faithfulness,
                        judge_model="synthetic",
                    )
                )

    scores = await daily_scores(sessions, since=TODAY - timedelta(days=40))
    report = detect_drift(scores, today=TODAY)

    assert report.alert
    assert report.status == "drift+slo_breach"
    assert (report.recent_n, report.baseline_n) == (28, 112)
