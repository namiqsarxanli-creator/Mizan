"""Calibrating the semantic cache's similarity threshold (F9).

Protocol: evals/results/f9_cache/PREREGISTRATION.md. The pure parts live here (pairs, split,
threshold rule, workload simulation); ``mizan-eval cache`` supplies the paraphrases, the
embeddings and the retrieval.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from mizan_core.logging import get_logger
from mizan_retrieval.evaluation import bootstrap

log = get_logger(__name__)

MAX_FALSE_HIT = 0.01  # pre-registered

PARAPHRASE_PROMPT = """\
Aşağıdakı hər sual üçün mənasını və hüquqi vəziyyətini tam saxlayan {n} fərqli parafraz yaz. \
Adi insanın yazacağı kimi yaz: başqa sözlərlə, başqa cümlə quruluşu ilə; heç bir şərti, rəqəmi \
və ya tərəfi dəyişmə, əlavə etmə, çıxarma.

{questions}

Yalnız JSON qaytar: {{"items": [{{"id": "q001", "paraphrases": ["...", "..."]}}]}}"""


def half(question_id: str) -> str:
    """A stable calibration / evaluation split of a question id."""
    bucket = int(hashlib.sha256(f"cache:{question_id}".encode()).hexdigest()[:8], 16)
    return "calib" if bucket % 2 == 0 else "eval"


@dataclass(frozen=True, slots=True)
class Pair:
    a: str  # question ids or "id#n" for a paraphrase
    b: str
    similarity: float
    positive: bool  # same question (paraphrase) vs a different question


def near_misses(gold: dict[str, frozenset[str]]) -> list[tuple[str, str]]:
    """Different questions sharing at least one gold article."""
    ids = sorted(gold)
    return [(a, b) for i, a in enumerate(ids) for b in ids[i + 1 :] if gold[a] & gold[b]]


def pick_threshold(pairs: Sequence[Pair], max_false_hit: float = MAX_FALSE_HIT) -> float:
    """The smallest threshold whose false-hit rate on the negatives is at most ``max_false_hit``."""
    negatives = sorted((p.similarity for p in pairs if not p.positive), reverse=True)
    if not negatives:
        raise ValueError("no negative pairs to calibrate on")
    allowed = int(max_false_hit * len(negatives))  # this many negatives may score >= threshold
    # Just above the (allowed+1)-th highest negative: it and everything below it miss.
    return float(np.nextafter(negatives[allowed], 1.0))


def rates(pairs: Sequence[Pair], threshold: float) -> dict[str, Any]:
    pos = [float(p.similarity >= threshold) for p in pairs if p.positive]
    neg = [float(p.similarity >= threshold) for p in pairs if not p.positive]
    hit, false_hit = bootstrap(pos), bootstrap(neg)
    return {
        "paraphrase_hit_rate": [round(hit.mean, 3), round(hit.low, 3), round(hit.high, 3)],
        "near_miss_false_hit_rate": [
            round(false_hit.mean, 3),
            round(false_hit.low, 3),
            round(false_hit.high, 3),
        ],
        "positives": len(pos),
        "negatives": len(neg),
    }


def simulate(
    phrasings: dict[str, list[str]],
    vectors: dict[str, np.ndarray],
    still_retrieved: Callable[[str, str], bool],
    threshold: float,
    *,
    seed: int = 7,
) -> dict[str, Any]:
    """Each question asked in all its phrasings, interleaved at random.

    A request hits when an earlier request's phrasing is similar enough and its sources are
    retrieved again for this one (``still_retrieved(earlier, now)``). ``phrasings`` maps a
    question id to the ids of its phrasings (the question itself first).
    """
    rng = random.Random(seed)  # noqa: S311 - simulation, not security
    order = [(qid, p) for qid, ps in phrasings.items() for p in ps]
    rng.shuffle(order)
    cached: list[tuple[str, str]] = []  # (question id, phrasing id) answered by the LLM
    hits = false_hits = 0
    for qid, phrasing in order:
        best = max(
            cached,
            key=lambda c: float(vectors[c[1]] @ vectors[phrasing]),
            default=None,
        )
        if (
            best is not None
            and float(vectors[best[1]] @ vectors[phrasing]) >= threshold
            and still_retrieved(best[1], phrasing)
        ):
            hits += 1
            false_hits += best[0] != qid
        else:
            cached.append((qid, phrasing))
    total = len(order)
    return {
        "requests": total,
        "llm_calls": total - hits,
        "cache_hits": hits,
        "false_hits": false_hits,
        "saving": round(hits / total, 3) if total else 0.0,
        "ceiling": round(1 - len(phrasings) / total, 3) if total else 0.0,
    }


def parse_paraphrases(content: str) -> dict[str, list[str]]:
    start, end = content.find("{"), content.rfind("}")
    try:
        items = json.loads(content[start : end + 1]).get("items", [])
    except (ValueError, AttributeError):
        return {}
    out = {}
    for item in items:
        ps = [
            p.strip()
            for p in item.get("paraphrases", [])
            if isinstance(p, str) and len(p.strip()) >= 8
        ]
        if item.get("id") and ps:
            out[str(item["id"])] = ps
    return out
