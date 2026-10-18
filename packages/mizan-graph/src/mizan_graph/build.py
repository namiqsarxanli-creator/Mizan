"""Derive graph edges from the temporal corpus.

A citation lives in a specific text of an article, and that text is valid on an interval.
Each ``(citing article, cited act, cited article)`` edge therefore carries the union of the
intervals of the texts that contain it, merged into maximal ranges: "art. 76 cites art. 70 on
[1999-02-01, ∞)" rather than fourteen per-version copies.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from mizan_graph.citations import SELF, extract
from mizan_retrieval.corpus import ChunkRecord


@dataclass(frozen=True, slots=True)
class Reference:
    source: str  # citing article key, "art:76"
    act: str  # SELF or the cited act's normalised name
    target: str  # cited article key, "art:70"
    numbers: tuple[str, ...]  # as cited, e.g. ("70",) or ("10-1.2", "10-1.3")
    valid_from: date
    valid_to: date  # exclusive

    @property
    def internal(self) -> bool:
        return self.act == SELF


@dataclass(slots=True)
class BuildStats:
    citations: int = 0
    self_loops: int = 0
    unresolved: list[tuple[str, str, date]] = field(default_factory=list)


def _body(text: str) -> str:
    """Chunk text without its breadcrumb line ("Code > Section > ... > Maddə 70. Heading")."""
    _, _, body = text.partition("\n")
    return body


def _merge(intervals: Iterable[tuple[date, date]]) -> list[tuple[date, date]]:
    merged: list[tuple[date, date]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def build_references(
    records: Iterable[ChunkRecord], *, stats: BuildStats | None = None
) -> list[Reference]:
    records = list(records)
    stats = stats if stats is not None else BuildStats()
    # Which article keys exist on which intervals, to flag citations of absent articles.
    present: dict[str, list[tuple[date, date]]] = defaultdict(list)
    for r in records:
        present[r.article_key].append((r.valid_from, r.valid_to))

    spans: dict[tuple[str, str, str], list[tuple[date, date]]] = defaultdict(list)
    numbers: dict[tuple[str, str, str], dict[str, None]] = defaultdict(dict)
    for r in records:
        for c in extract(_body(r.text)):
            stats.citations += 1
            target = f"art:{c.article}"
            if c.internal and target == r.article_key:
                stats.self_loops += 1
                continue
            if c.internal and not any(s <= r.valid_from < e for s, e in present[target]):
                stats.unresolved.append((r.article_key, c.number, r.valid_from))
            identity = (r.article_key, c.act, target)
            spans[identity].append((r.valid_from, r.valid_to))
            numbers[identity][c.number] = None

    references = [
        Reference(source, act, target, tuple(numbers[(source, act, target)]), start, end)
        for (source, act, target), intervals in spans.items()
        for start, end in _merge(intervals)
    ]
    return sorted(references, key=lambda ref: (ref.source, ref.act, ref.target, ref.valid_from))
