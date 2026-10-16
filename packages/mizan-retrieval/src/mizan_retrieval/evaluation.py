"""Retrieval metrics with bootstrap confidence intervals.

Metrics are computed at **article** level: a question is answered by citing an article, and the
same article may come back as several chunks. Hits are de-duplicated by article in rank order
before scoring.

With ~70 questions, one question is ~1.4 points of Hit@5. Point estimates alone would invite
over-reading noise, so every metric carries a 95% bootstrap interval (resampling questions).
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

TODAY = date(2026, 10, 13)


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    question: str
    relevant: frozenset[str]
    type: str
    as_of: date


def load_dataset(path: Path, *, today: date = TODAY) -> list[Question]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        out.append(
            Question(
                id=row["id"],
                question=row["question"],
                relevant=frozenset(row["relevant"]),
                type=row["type"],
                as_of=date.fromisoformat(row["as_of"]) if "as_of" in row else today,
            )
        )
    return out


def dedupe(article_keys: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(article_keys))


def hit_at(ranked: Sequence[str], relevant: frozenset[str], k: int) -> float:
    return float(any(r in relevant for r in ranked[:k]))


def recall_at(ranked: Sequence[str], relevant: frozenset[str], k: int) -> float:
    return len(relevant.intersection(ranked[:k])) / len(relevant)


def mrr_at(ranked: Sequence[str], relevant: frozenset[str], k: int = 10) -> float:
    for i, key in enumerate(ranked[:k], start=1):
        if key in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at(ranked: Sequence[str], relevant: frozenset[str], k: int = 10) -> float:
    dcg = sum(1.0 / math.log2(i + 1) for i, key in enumerate(ranked[:k], 1) if key in relevant)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0


METRICS: dict[str, Callable[[Sequence[str], frozenset[str]], float]] = {
    "Hit@1": lambda r, rel: hit_at(r, rel, 1),
    "Hit@5": lambda r, rel: hit_at(r, rel, 5),
    "Recall@5": lambda r, rel: recall_at(r, rel, 5),
    "Recall@10": lambda r, rel: recall_at(r, rel, 10),
    "MRR@10": lambda r, rel: mrr_at(r, rel, 10),
    "nDCG@10": lambda r, rel: ndcg_at(r, rel, 10),
}


@dataclass(frozen=True, slots=True)
class Estimate:
    mean: float
    low: float
    high: float

    def __str__(self) -> str:
        return f"{self.mean:.3f} [{self.low:.2f}, {self.high:.2f}]"


def bootstrap(values: Sequence[float], *, rounds: int = 2000, seed: int = 7) -> Estimate:
    if not values:
        return Estimate(0.0, 0.0, 0.0)
    rng = random.Random(seed)  # noqa: S311 - statistics, not security; seeded for reproducibility
    n = len(values)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(rounds))
    return Estimate(
        mean=sum(values) / n,
        low=means[int(0.025 * rounds)],
        high=means[int(0.975 * rounds) - 1],
    )


@dataclass
class RunResult:
    label: str
    per_question: dict[str, dict[str, float]] = field(default_factory=dict)
    latency_ms: list[float] = field(default_factory=list)

    def add(self, question: Question, ranked_articles: Sequence[str], latency_ms: float) -> None:
        ranked = dedupe(ranked_articles)
        self.per_question[question.id] = {
            name: fn(ranked, question.relevant) for name, fn in METRICS.items()
        }
        self.latency_ms.append(latency_ms)

    def summary(self, ids: Sequence[str] | None = None) -> dict[str, Estimate]:
        chosen = [self.per_question[i] for i in (ids or self.per_question)]
        return {name: bootstrap([q[name] for q in chosen]) for name in METRICS}

    @property
    def p50_latency_ms(self) -> float:
        ordered = sorted(self.latency_ms)
        return ordered[len(ordered) // 2] if ordered else 0.0
