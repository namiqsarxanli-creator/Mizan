"""Training data for the retrieval models: split, leakage guard, hard negatives.

**Split by article, not by question.** Synthetic questions about one article are near
paraphrases of each other. A question-level split would put siblings on both sides and
measure memorisation. The test articles are never seen in training.

**No leakage from the evaluation sets.** Every synthetic question is compared with every
question of the evaluation datasets (v0, v1, multi-hop, court decisions). Questions that are
near duplicates (character 3-gram Jaccard ≥ 0.5) are dropped, and the dropped count is
reported. The evaluation datasets themselves are never training data.

**Hard negatives** come from the base model: articles ranked high for the question that are
not its article. Articles linked to the gold article by a citation edge are excluded,
because they are often partly relevant ("severance" questions cite Art. 70 as well as 77).
Taking negatives from ranks 3-30 rather than 1-2 reduces false negatives further.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mizan_ingestion.text import az_lower
from mizan_train.synth import Pair


@dataclass(frozen=True, slots=True)
class Example:
    question: str
    positive: str  # article key
    negatives: tuple[str, ...] = ()  # article keys, hardest first
    split: str = "train"  # train | val | test


@dataclass(slots=True)
class BuildReport:
    pairs: int = 0
    rejected_by_judge: int = 0
    leaked: int = 0
    examples: dict[str, int] = field(default_factory=dict)
    articles: dict[str, int] = field(default_factory=dict)


def article_split(key: str, *, val: float = 0.1, test: float = 0.1, seed: str = "mizan") -> str:
    """Stable split of an article key: the same article always lands in the same split."""
    bucket = int(hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    if bucket < test:
        return "test"
    if bucket < test + val:
        return "val"
    return "train"


def _grams(text: str, n: int = 3) -> set[str]:
    t = " ".join(az_lower(text).split())
    return {t[i : i + n] for i in range(max(len(t) - n + 1, 1))}


def jaccard(a: str, b: str) -> float:
    ga, gb = _grams(a), _grams(b)
    return len(ga & gb) / len(ga | gb) if ga and gb else 0.0


def leaks(question: str, protected: Sequence[str], threshold: float = 0.5) -> bool:
    return any(jaccard(question, p) >= threshold for p in protected)


def hard_negatives(
    ranked: Sequence[str],
    positive: str,
    *,
    related: Collection[str] = (),
    start: int = 2,
    k: int = 5,
) -> tuple[str, ...]:
    """Base-model ranking -> negatives: skip the top ``start`` ranks, the gold article and its
    citation neighbours."""
    picked: list[str] = []
    for key in ranked[start:]:
        if key != positive and key not in related and key not in picked:
            picked.append(key)
        if len(picked) == k:
            break
    return tuple(picked)


def build_examples(
    pairs: Iterable[Pair],
    protected_questions: Sequence[str],
    *,
    report: BuildReport | None = None,
) -> list[Example]:
    report = report if report is not None else BuildReport()
    examples = []
    for p in pairs:
        report.pairs += 1
        if not p.kept:
            report.rejected_by_judge += 1
            continue
        if leaks(p.question, protected_questions):
            report.leaked += 1
            continue
        examples.append(Example(p.question, p.article, split=article_split(p.article)))
    for split in ("train", "val", "test"):
        mine = [e for e in examples if e.split == split]
        report.examples[split] = len(mine)
        report.articles[split] = len({e.positive for e in mine})
    return examples


def write_examples(path: Path, examples: Sequence[Example]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for e in examples:
            fh.write(json.dumps(asdict(e), ensure_ascii=False) + "\n")


def read_examples(path: Path) -> list[Example]:
    rows = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line)
    return [Example(r["question"], r["positive"], tuple(r["negatives"]), r["split"]) for r in rows]
