"""End-to-end generation eval: retrieve -> answer -> judge, one checkpointed record per question.

Deterministic metrics are computed in code; only answer correctness is judged by an LLM:

- ``cites_gold``      does any verified citation point into a gold article?
- ``citation_valid``  share of the model's citations that were verbatim quotes of its sources
- ``answer_correct``  judge verdict against the gold text in force on the question's date
- abstention          for out-of-scope questions, "correct" means the system declined

The run is paced for provider rate limits (Groq's free tier: 8k tokens per minute and model)
and resumable: an interrupted run continues from the last finished question.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from mizan_core.logging import get_logger
from mizan_evals.judge import JUDGE_VERSION, GroqJudge
from mizan_retrieval.answer import Answer, AnswerBackend, AnswerUnavailableError
from mizan_retrieval.evaluation import Question, bootstrap
from mizan_retrieval.search import Hit, SearchBackend, SearchConfig

log = get_logger(__name__)

GoldLoader = Callable[[Sequence[str], date], Awaitable[str]]
# Gold beyond the statute text for one question (court decisions, an expected conclusion).
ExtraGold = Callable[[Question], Awaitable[str]]
Expander = Callable[[Sequence[Hit], date], Awaitable[list[Hit]]]


@dataclass
class Record:
    id: str
    type: str
    as_of: str
    question: str
    gold: list[str]
    sources: list[str] = field(default_factory=list)
    answer: str = ""
    citations: list[dict[str, str]] = field(default_factory=list)
    rejected_citations: int = 0
    answer_model: str = ""
    refused: bool = False
    cites_gold: bool | None = None
    citation_valid: float | None = None
    correct: bool | None = None
    abstained: bool | None = None
    reason: str = ""
    judge_model: str = ""
    judge_version: int = JUDGE_VERSION
    latency_ms: float = 0.0
    error: str | None = None


def stratified_sample(questions: Sequence[Question], n: int, *, seed: int = 13) -> list[Question]:
    """Pick ~n questions with every type represented in proportion (at least 2 per type)."""
    if n >= len(questions):
        return list(questions)
    by_type: dict[str, list[Question]] = defaultdict(list)
    for q in questions:
        by_type[q.type].append(q)
    rng = random.Random(seed)  # noqa: S311 - sampling for evaluation, not security
    chosen: list[Question] = []
    for qs in by_type.values():
        k = max(2, round(n * len(qs) / len(questions)))
        chosen += rng.sample(qs, min(k, len(qs)))
    return sorted(chosen, key=lambda q: q.id)


def cites_gold(citations: Sequence[dict[str, str]], gold: Sequence[str]) -> bool | None:
    if not gold:
        return None  # out-of-scope question: nothing to cite
    return any(c["key"].split("/", 1)[0] in gold for c in citations)


def citation_valid(answer: Answer) -> float | None:
    total = len(answer.citations) + answer.rejected_citations
    return len(answer.citations) / total if total else None


def load_records(path: Path) -> dict[str, Record]:
    if not path.exists():
        return {}
    records = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            records[row["id"]] = Record(**row)
    return records


def _append(path: Path, record: Record) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def _done(record: Record | None) -> bool:
    return (
        record is not None
        and record.error is None
        and record.correct is not None
        and record.judge_version == JUDGE_VERSION
    )


async def _answer_with_retry(
    answerer: AnswerBackend, q: Question, hits: list[Hit], attempts: int = 4
) -> Answer:
    for attempt in range(attempts):
        try:
            return await answerer.answer(q.question, hits, as_of=q.as_of)
        except AnswerUnavailableError as exc:
            if attempt == attempts - 1:
                raise
            wait = exc.retry_after_s or 30 * (attempt + 1)
            log.warning("answer_retry", question=q.id, wait_s=wait, error=str(exc))
            await asyncio.sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


async def run_generation_eval(
    questions: Sequence[Question],
    *,
    searcher: SearchBackend,
    config: SearchConfig,
    expand: Expander,
    answerer: AnswerBackend,
    judge: GroqJudge,
    gold_text: GoldLoader,
    source_id: int,
    out: Path,
    pace_s: float = 0.0,
    k: int = 6,
    extra_gold: ExtraGold | None = None,
) -> dict[str, Record]:
    records = await asyncio.to_thread(load_records, out)
    for i, q in enumerate(questions, start=1):
        if _done(records.get(q.id)):
            continue
        record = Record(
            id=q.id,
            type=q.type,
            as_of=q.as_of.isoformat(),
            question=q.question,
            gold=sorted(q.relevant),
        )
        started = time.perf_counter()
        try:
            hits = await searcher.search(
                q.question, source_id=source_id, as_of=q.as_of, config=config, k=k
            )
            hits = await expand(hits, q.as_of)
            answer = await _answer_with_retry(answerer, q, hits)
            record.sources = [h.key for h in hits]
            record.answer = answer.text
            record.citations = [{"key": c.key, "quote": c.quote} for c in answer.citations]
            record.rejected_citations = answer.rejected_citations
            record.answer_model = answer.model
            record.refused = answer.refused
            record.cites_gold = cites_gold(record.citations, record.gold)
            record.citation_valid = citation_valid(answer)
            record.latency_ms = round((time.perf_counter() - started) * 1000, 1)
            verdict = await judge.judge(
                question=q.question,
                qtype=q.type,
                as_of=q.as_of,
                gold="\n\n".join(
                    part
                    for part in (
                        await gold_text(record.gold, q.as_of),
                        await extra_gold(q) if extra_gold else "",
                    )
                    if part
                ),
                answer=answer.text,
            )
            record.correct = verdict.correct
            record.abstained = verdict.abstained
            record.reason = verdict.reason
            record.judge_model = verdict.model
        except Exception as exc:  # noqa: BLE001 - keep going; the record says what failed
            record.error = f"{type(exc).__name__}: {exc}"
            log.warning("geneval_question_failed", question=q.id, error=record.error)
        records[q.id] = record
        await asyncio.to_thread(_append, out, record)
        log.info(
            "geneval_progress", done=i, total=len(questions), question=q.id, correct=record.correct
        )
        if pace_s and i < len(questions):
            await asyncio.sleep(pace_s)
    return records


def summarise(records: Sequence[Record]) -> dict[str, Any]:

    ok = [r for r in records if r.error is None and r.correct is not None]
    by_type: dict[str, list[Record]] = defaultdict(list)
    for r in ok:
        by_type[r.type].append(r)

    def rate(values: list[bool]) -> dict[str, float]:
        est = bootstrap([float(v) for v in values])
        return {"mean": est.mean, "low": est.low, "high": est.high, "n": len(values)}

    in_scope = [r for r in ok if r.type != "no_answer"]
    valid = [r.citation_valid for r in ok if r.citation_valid is not None]
    return {
        "questions": len(records),
        "failed": len(records) - len(ok),
        "answer_correct": rate([bool(r.correct) for r in ok]),
        "answer_correct_in_scope": rate([bool(r.correct) for r in in_scope]),
        "abstention_accuracy": rate([bool(r.correct) for r in by_type.get("no_answer", [])]),
        "cites_gold": rate([bool(r.cites_gold) for r in in_scope]),
        "citation_valid": sum(valid) / len(valid) if valid else None,
        "by_type": {t: rate([bool(r.correct) for r in rs]) for t, rs in sorted(by_type.items())},
        "p50_latency_ms": sorted(r.latency_ms for r in ok)[len(ok) // 2] if ok else None,
    }
