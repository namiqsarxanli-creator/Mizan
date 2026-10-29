"""Shadow comparison report (F9): candidate vs primary on the same real requests.

Reads ``shadow_runs`` joined to the primary ``answers``. The same numbers are on the Grafana
dashboard (ADR-0008); this renders them as a dated Markdown report for a switch decision.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mizan_core.genai import PRICES
from mizan_ingestion.db.quality import AnswerLog, ShadowRun
from mizan_retrieval.evaluation import bootstrap


@dataclass(frozen=True, slots=True)
class Row:
    candidate: str
    primary_model: str | None
    tokens: int
    primary_tokens: int
    latency_ms: float
    primary_latency_ms: float
    citation_overlap: float
    refusal_agrees: bool
    failed: bool = False


async def load_rows(session: AsyncSession, since: datetime | None = None) -> list[Row]:
    query = select(ShadowRun, AnswerLog.model).join(AnswerLog, AnswerLog.id == ShadowRun.answer_id)
    if since is not None:
        query = query.where(ShadowRun.created_at >= since)
    return [
        Row(
            s.candidate,
            model,
            s.tokens,
            s.primary_tokens,
            s.latency_ms,
            s.primary_latency_ms,
            s.citation_overlap,
            s.refusal_agrees,
            s.error is not None,
        )
        for s, model in (await session.execute(query)).all()
    ]


def _p(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 1)


def _blended_price(model: str | None) -> float | None:
    """USD per 1M tokens, input and output averaged (shadow rows store totals only)."""
    price = PRICES.get(model or "")
    return (price[0] + price[1]) / 2 if price else None


def summarise(rows: Sequence[Row]) -> dict[str, Any]:
    by_candidate: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_candidate[r.candidate].append(r)
    out: dict[str, Any] = {}
    for candidate, every in sorted(by_candidate.items()):
        failure_rate = statistics.fmean(r.failed for r in every)
        rs = [r for r in every if not r.failed]  # agreement and cost: answered requests only
        if not rs:
            out[candidate] = {"requests": len(every), "failure_rate": 1.0}
            continue
        overlap = bootstrap([r.citation_overlap for r in rs])
        agree = bootstrap([float(r.refusal_agrees) for r in rs])
        primary_price = _blended_price(rs[0].primary_model)
        candidate_price = _blended_price(candidate)
        cost: dict[str, float] | None = None
        if primary_price is not None and candidate_price is not None:
            primary = statistics.fmean(r.primary_tokens for r in rs) * primary_price / 1e6
            shadow = statistics.fmean(r.tokens for r in rs) * candidate_price / 1e6
            cost = {
                "primary_usd": round(primary, 6),
                "candidate_usd": round(shadow, 6),
                "ratio": round(shadow / primary, 3) if primary else 0.0,
            }
        out[candidate] = {
            "requests": len(every),
            "failure_rate": round(failure_rate, 3),
            "primary_model": rs[0].primary_model,
            "citation_overlap": [
                round(overlap.mean, 3),
                round(overlap.low, 3),
                round(overlap.high, 3),
            ],
            "same_citations": round(statistics.fmean(r.citation_overlap == 1.0 for r in rs), 3),
            "refusal_agreement": [round(agree.mean, 3), round(agree.low, 3), round(agree.high, 3)],
            "latency_ms": {
                "primary_p50": _p([r.primary_latency_ms for r in rs], 0.5),
                "candidate_p50": _p([r.latency_ms for r in rs], 0.5),
                "primary_p95": _p([r.primary_latency_ms for r in rs], 0.95),
                "candidate_p95": _p([r.latency_ms for r in rs], 0.95),
            },
            "tokens_mean": {
                "primary": round(statistics.fmean(r.primary_tokens for r in rs)),
                "candidate": round(statistics.fmean(r.tokens for r in rs)),
            },
            "cost_per_request": cost,
        }
    return out


def render(summary: dict[str, Any], today: date) -> str:
    lines = [f"# Shadow report — {today.isoformat()}", ""]
    if not summary:
        return "\n".join([*lines, "No shadow runs in the window.", ""])
    for candidate, s in summary.items():
        if "latency_ms" not in s:
            lines += [f"## `{candidate}`: failed on all {s['requests']} requests", ""]
            continue
        lat, tok, cost = s["latency_ms"], s["tokens_mean"], s["cost_per_request"]
        lines += [
            f"## `{candidate}` vs `{s['primary_model']}` ({s['requests']} requests)",
            "",
            "| | primary | candidate |",
            "|---|---|---|",
            f"| latency p50 / p95 (ms) | {lat['primary_p50']} / {lat['primary_p95']} | "
            f"{lat['candidate_p50']} / {lat['candidate_p95']} |",
            f"| tokens per request | {tok['primary']} | {tok['candidate']} |",
        ]
        if cost:
            lines.append(
                f"| est. cost per request (USD) | {cost['primary_usd']} | {cost['candidate_usd']} "
                f"({cost['ratio']}x) |"
            )
        ov, ag = s["citation_overlap"], s["refusal_agreement"]
        lines += [
            "",
            f"- Citation overlap (Jaccard): **{ov[0]}** [{ov[1]}, {ov[2]}]; identical citation "
            f"sets: {s['same_citations']:.0%}",
            f"- Refusal agreement: **{ag[0]}** [{ag[1]}, {ag[2]}]",
            f"- Candidate failures (provider error, invalid output): **{s['failure_rate']:.0%}**",
            "",
            "Agreement is not correctness: disagreements are where to look. The candidate's "
            "faithfulness comes from the online judge on its own sample.",
            "",
        ]
    return "\n".join(lines)
