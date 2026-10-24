"""Agent vs baseline RAG on the same questions (F5 DoD).

For each question both systems answer, and each answer is measured three ways:

- ``correct``         the F3 judge against the gold provisions (and decisions) in force
- ``faithfulness``    an *independent* model lists the answer's legal statements and checks
                      each against the sources the system itself was given (not the gold):
                      does the answer say only what its sources say?
- ``citation_ok``     share of citations whose quote occurs verbatim in the cited source
                      (computed in code)

Three different models are involved on purpose: the drafter (gpt-oss-120b), the correctness
judge (gpt-oss-20b, as in F3) and the faithfulness judge (qwen3.8-27b), so that no model grades
its own output. The agent's own verifier also runs on gpt-oss-20b; the correctness judge sees
the gold, the verifier sees only the sources, so they answer different questions.

Abstentions are reported separately (coverage, selective accuracy): an agent that abstains
more must not look better on faithfulness for it.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from mizan_agents.schemas import Schema
from mizan_core.logging import get_logger
from mizan_evals.judge import Verdict
from mizan_retrieval.evaluation import Question, bootstrap

log = get_logger(__name__)

DEFAULT_FAITHFULNESS_MODEL = "qwen/qwen3.8-27b"
FAITHFULNESS_VERSION = 1

FAITHFULNESS_PROMPT = """You check whether an answer from a legal research assistant says only \
what its sources say. The sources are Azerbaijani legal texts; the answer is in Azerbaijani.

Sources given to the assistant:
{sources}

Answer:
{answer}

List every legal statement the answer makes (rules, deadlines, amounts, conditions, \
exceptions). Ignore disclaimers and advice to consult a lawyer. For each statement decide \
whether the sources support it. Use only the sources, not your own knowledge.

Return a JSON object only:
{{"statements": [{{"text": "<statement>", "supported": true|false}}]}}"""


class _Statement(Schema):
    text: str
    supported: bool


class _Statements(Schema):
    statements: list[_Statement] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SystemOutput:
    """What one system produced for one question, in comparable terms."""

    answer: str
    abstained: bool
    citations_total: int
    citations_verbatim: int
    sources: list[tuple[str, str]]  # (key, text) given to the model
    confidence: float | None = None
    tokens: int = 0
    # Per citation: {"source", "quote", "verbatim", "support"?}, for error analysis.
    claims: list[dict[str, object]] = field(default_factory=list)


AnswerFn = Callable[[Question], Awaitable[SystemOutput]]


class CorrectnessJudge(Protocol):
    async def judge(
        self, *, question: str, qtype: str, as_of: date, gold: str, answer: str
    ) -> Verdict: ...


class FaithfulnessChecker(Protocol):
    async def judge(
        self, answer: str, sources: Sequence[tuple[str, str]]
    ) -> tuple[float, list[str]]: ...


class FaithfulnessJudge:
    def __init__(self, client: Any, *, model: str = DEFAULT_FAITHFULNESS_MODEL) -> None:
        self._client = client
        self.model = model

    async def judge(
        self, answer: str, sources: Sequence[tuple[str, str]]
    ) -> tuple[float, list[str]]:
        rendered = "\n\n".join(f'<source key="{k}">\n{t[:3000]}\n</source>' for k, t in sources)
        prompt = FAITHFULNESS_PROMPT.format(sources=rendered[:14000], answer=answer)
        last: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                    max_completion_tokens=2048,
                )
                parsed = _Statements.model_validate(
                    json.loads(response.choices[0].message.content or "")
                )
                if not parsed.statements:
                    return 1.0, []  # nothing claimed, nothing unfaithful
                unsupported = [s.text for s in parsed.statements if not s.supported]
                return 1 - len(unsupported) / len(parsed.statements), unsupported
            except Exception as exc:  # noqa: BLE001 - rate limits, malformed JSON: retry
                last = exc
                await asyncio.sleep(10 * (attempt + 1))
        raise RuntimeError(f"faithfulness judge failed: {last!r}")


@dataclass
class AgentRecord:
    system: str
    id: str
    type: str
    as_of: str
    question: str
    answer: str = ""
    abstained: bool = False
    citations_total: int = 0
    citations_verbatim: int = 0
    sources: list[str] = field(default_factory=list)
    confidence: float | None = None
    tokens: int = 0
    claims: list[dict[str, object]] = field(default_factory=list)
    faithfulness: float | None = None
    unsupported: list[str] = field(default_factory=list)
    correct: bool | None = None
    reason: str = ""
    latency_ms: float = 0.0
    faithfulness_version: int = FAITHFULNESS_VERSION
    error: str | None = None

    @property
    def citation_ok(self) -> float | None:
        return self.citations_verbatim / self.citations_total if self.citations_total else None


def load_agent_records(path: Path) -> dict[tuple[str, str], AgentRecord]:
    if not path.exists():
        return {}
    records = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = AgentRecord(**json.loads(line))
            records[(r.system, r.id)] = r
    return records


def _append(path: Path, record: AgentRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


async def run_agent_eval(
    questions: Sequence[Question],
    systems: dict[str, AnswerFn],
    *,
    judge: CorrectnessJudge,
    faithfulness: FaithfulnessChecker,
    gold: Callable[[Question], Awaitable[str]],
    out: Path,
    pace_s: float = 0.0,
) -> dict[tuple[str, str], AgentRecord]:
    records = await asyncio.to_thread(load_agent_records, out)
    for i, q in enumerate(questions, start=1):
        for name, system in systems.items():
            done = records.get((name, q.id))
            if done is not None and done.error is None and done.correct is not None:
                continue
            record = AgentRecord(name, q.id, q.type, q.as_of.isoformat(), q.question)
            started = time.perf_counter()
            try:
                output = await system(q)
                record.latency_ms = round((time.perf_counter() - started) * 1000, 1)
                record.answer = output.answer
                record.abstained = output.abstained
                record.citations_total = output.citations_total
                record.citations_verbatim = output.citations_verbatim
                record.sources = [k for k, _ in output.sources]
                record.confidence = output.confidence
                record.tokens = output.tokens
                record.claims = output.claims
                if not output.abstained:
                    record.faithfulness, record.unsupported = await faithfulness.judge(
                        output.answer, output.sources
                    )
                verdict = await judge.judge(
                    question=q.question,
                    qtype=q.type,
                    as_of=q.as_of,
                    gold=await gold(q),
                    answer=output.answer,
                )
                record.correct = verdict.correct
                record.reason = verdict.reason
            except Exception as exc:  # noqa: BLE001 - keep going; the record says what failed
                record.error = f"{type(exc).__name__}: {exc}"
                log.warning("agent_eval_failed", system=name, question=q.id, error=record.error)
            records[(name, q.id)] = record
            await asyncio.to_thread(_append, out, record)
        log.info("agent_eval_progress", done=i, total=len(questions), question=q.id)
        if pace_s and i < len(questions):
            await asyncio.sleep(pace_s)
    return records


def _mean(values: Sequence[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    e = bootstrap(list(values))
    return {
        "mean": round(e.mean, 3),
        "low": round(e.low, 3),
        "high": round(e.high, 3),
        "n": len(values),
    }


def summarise_agent_eval(records: Sequence[AgentRecord], systems: Sequence[str]) -> dict[str, Any]:
    ok = [r for r in records if r.error is None and r.correct is not None]
    out: dict[str, Any] = {}
    for name in systems:
        mine = [r for r in ok if r.system == name]
        in_scope = [r for r in mine if r.type not in ("no_answer",)]
        answered = [r for r in in_scope if not r.abstained]
        out[name] = {
            "questions": len(mine),
            "correct": _mean([float(r.correct or False) for r in mine]),
            "correct_in_scope": _mean([float(r.correct or False) for r in in_scope]),
            "coverage": _mean([float(not r.abstained) for r in in_scope]),
            "selective_accuracy": _mean([float(r.correct or False) for r in answered]),
            "faithfulness": _mean([r.faithfulness for r in answered if r.faithfulness is not None]),
            "citation_accuracy": _mean([c for r in answered if (c := r.citation_ok) is not None]),
            "p50_latency_ms": sorted(r.latency_ms for r in mine)[len(mine) // 2] if mine else None,
            "mean_tokens": round(sum(r.tokens for r in mine) / len(mine)) if mine else None,
        }
    if len(systems) == 2:
        a, b = systems
        by = {(r.system, r.id): r for r in ok}
        shared = sorted({r.id for r in ok if (a, r.id) in by and (b, r.id) in by})
        deltas = [
            float(by[(b, i)].correct or False) - float(by[(a, i)].correct or False) for i in shared
        ]
        if deltas:
            e = bootstrap(deltas)
            out["paired_correct"] = {
                "baseline": a,
                "candidate": b,
                "delta": round(e.mean, 3),
                "low": round(e.low, 3),
                "high": round(e.high, 3),
                "n": len(deltas),
                "significant": e.low > 0 or e.high < 0,
            }
    return out
