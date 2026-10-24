"""LLM-as-judge for answer correctness, with type-aware rules.

The judge sees the question, the date it is asked about, the *gold* provisions as they stood on
that date, and the system's answer. It never sees which system produced the answer, and it
runs on a different model from the answerer (self-preference bias is well documented).

What the judge decides is deliberately narrow: is the answer right according to the gold text?
Everything that can be checked mechanically (are quotes verbatim, does the answer cite a gold
article) is computed in code, not asked of a model.

A judge is only trustworthy once calibrated against human labels (``mizan-eval kappa``); until
then its scores are a signal, not a measurement.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date
from typing import Any

DEFAULT_JUDGE_MODEL = "openai/gpt-oss-20b"
JUDGE_VERSION = 1  # bump when the prompt or rules change; cached verdicts are invalidated

RULES = {
    "no_answer": (
        "The question is OUTSIDE the scope of the Labour Code. The answer is correct only if it "
        "says the provided legislation does not answer the question (it may suggest where to "
        "look) and does NOT present rules from other laws as if they came from its sources."
    ),
    "false_premise": (
        "The question contains a FALSE premise. The answer is correct only if it explicitly "
        "rejects the premise and states the correct rule from the gold provisions."
    ),
    "court_decision": (
        "The gold includes the Constitutional Court decisions in force on the date and the "
        "expected conclusion. A decision controls how the provisions apply: it may suspend part "
        "of an article or fix its interpretation. The answer is correct only if its conclusion "
        "agrees with the expected conclusion; applying an article's literal text where a "
        "decision changes it is incorrect."
    ),
    "default": (
        "The answer is correct if its legal conclusion agrees with the gold provisions in force "
        "on the given date and it does not omit a condition that changes the conclusion. Extra "
        "correct detail is fine; any statement contradicting the gold provisions makes it "
        "incorrect. Judge substance, not style or language quality."
    ),
}

PROMPT = """You are grading an answer from a legal research assistant for Azerbaijani labour law.

Date the question is about: {as_of}
Question type: {qtype}
Rule for this type: {rule}

Question:
{question}

Gold provisions (Azerbaijani Labour Code, text in force on that date):
{gold}

Answer to grade:
{answer}

Return a JSON object only:
{{"correct": true|false, "abstained": true|false, "reason": "<one sentence, English>"}}
- "abstained": the answer declines to answer or says the sources do not cover the question."""


@dataclass(frozen=True, slots=True)
class Verdict:
    correct: bool
    abstained: bool
    reason: str
    model: str
    judge_version: int = JUDGE_VERSION


class JudgeError(RuntimeError):
    pass


def build_prompt(*, question: str, qtype: str, as_of: date, gold: str, answer: str) -> str:
    return PROMPT.format(
        as_of=as_of.isoformat(),
        qtype=qtype,
        rule=RULES.get(qtype, RULES["default"]),
        question=question,
        gold=gold or "(none: no provision of the Labour Code answers this question)",
        answer=answer,
    )


def parse_verdict(content: str, model: str) -> Verdict:
    try:
        data = json.loads(content)
        return Verdict(
            correct=bool(data["correct"]),
            abstained=bool(data.get("abstained", False)),
            reason=str(data.get("reason", "")),
            model=model,
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise JudgeError(f"unparseable verdict: {content[:200]!r}") from exc


class GroqJudge:
    def __init__(
        self, client: Any, *, model: str = DEFAULT_JUDGE_MODEL, max_attempts: int = 3
    ) -> None:
        """``client`` is a ``groq.AsyncGroq`` (typed loosely so tests can fake it)."""
        self._client = client
        self._model = model
        self._max_attempts = max_attempts

    @property
    def model(self) -> str:
        return self._model

    async def judge(
        self, *, question: str, qtype: str, as_of: date, gold: str, answer: str
    ) -> Verdict:
        prompt = build_prompt(question=question, qtype=qtype, as_of=as_of, gold=gold, answer=answer)
        last: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                    max_completion_tokens=1024,
                    **({"reasoning_effort": "medium"} if "gpt-oss" in self._model else {}),
                )
                return parse_verdict(response.choices[0].message.content or "", self._model)
            except JudgeError as exc:  # malformed output: ask again
                last = exc
            except Exception as exc:  # noqa: BLE001 - rate limits etc.: back off and retry
                last = exc
                await asyncio.sleep(min(60, 10 * (attempt + 1)))
        raise JudgeError(f"judge failed after {self._max_attempts} attempts: {last!r}")
