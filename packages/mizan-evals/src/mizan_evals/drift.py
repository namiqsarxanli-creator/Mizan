"""Quality drift: alert when online faithfulness falls.

Two signals, from the ``online_evals`` the API writes for a sample of answers:

- **drift**: the last 7 days' mean is more than 10% below the preceding 28 days' mean.
  This is a *relative* drop, which catches a regression even while quality is still "good".
- **SLO breach**: the last 7 days' mean is below the faithfulness SLO (0.90).

Both need enough samples (20 by default) on each side; with fewer, the check reports
"insufficient data" instead of guessing. Means are sample-weighted, not means of daily means.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mizan_ingestion.db.quality import OnlineEval

SLO_FAITHFULNESS = 0.90


@dataclass(frozen=True, slots=True)
class DailyScore:
    day: date
    mean: float
    n: int


@dataclass(frozen=True, slots=True)
class DriftReport:
    status: str  # "ok" | "drift" | "slo_breach" | "drift+slo_breach" | "insufficient_data"
    recent_mean: float | None
    recent_n: int
    baseline_mean: float | None
    baseline_n: int
    relative_drop: float | None

    @property
    def alert(self) -> bool:
        return self.status not in ("ok", "insufficient_data")

    def message(self) -> str:
        if self.status == "insufficient_data":
            return (
                f"Faithfulness: not enough samples "
                f"(recent {self.recent_n}, baseline {self.baseline_n})."
            )
        return (
            f"Faithfulness {self.status}: last 7 days {self.recent_mean:.3f} (n={self.recent_n}), "
            f"previous 28 days {self.baseline_mean:.3f} (n={self.baseline_n}), "
            f"drop {self.relative_drop:+.1%}."
        )


def _weighted(scores: Sequence[DailyScore]) -> tuple[float | None, int]:
    n = sum(s.n for s in scores)
    return (sum(s.mean * s.n for s in scores) / n if n else None), n


def detect_drift(
    scores: Sequence[DailyScore],
    *,
    today: date,
    window_days: int = 7,
    baseline_days: int = 28,
    max_drop: float = 0.10,
    min_samples: int = 20,
    slo: float = SLO_FAITHFULNESS,
) -> DriftReport:
    recent_from = today - timedelta(days=window_days - 1)
    baseline_from = recent_from - timedelta(days=baseline_days)
    recent = [s for s in scores if recent_from <= s.day <= today]
    baseline = [s for s in scores if baseline_from <= s.day < recent_from]
    recent_mean, recent_n = _weighted(recent)
    baseline_mean, baseline_n = _weighted(baseline)
    if recent_mean is None or recent_n < min_samples:
        return DriftReport(
            "insufficient_data", recent_mean, recent_n, baseline_mean, baseline_n, None
        )

    breach = recent_mean < slo
    drop = None
    drifted = False
    if baseline_mean is not None and baseline_n >= min_samples and baseline_mean > 0:
        drop = (recent_mean - baseline_mean) / baseline_mean
        drifted = drop < -max_drop
    status = {(True, True): "drift+slo_breach", (True, False): "drift", (False, True): "slo_breach"}
    return DriftReport(
        status.get((drifted, breach), "ok"), recent_mean, recent_n, baseline_mean, baseline_n, drop
    )


async def daily_scores(
    sessions: async_sessionmaker[AsyncSession], *, since: date
) -> list[DailyScore]:
    day = cast(OnlineEval.created_at, Date)
    async with sessions() as session:
        rows = await session.execute(
            select(day, func.avg(OnlineEval.faithfulness), func.count())
            .where(day >= since)
            .group_by(day)
            .order_by(day)
        )
        return [DailyScore(d, float(m), int(n)) for d, m, n in rows]
