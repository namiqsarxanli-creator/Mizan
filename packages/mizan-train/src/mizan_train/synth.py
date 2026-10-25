"""Synthetic training questions for the Labour Code (F7 week 15).

    article text ──LLM──▶ 4 questions a citizen would ask ──round-trip judge──▶ kept pairs

Generation and filtering use different models: the generator writes questions from the
article, a second model checks, *without seeing which article produced it*, whether the
article answers the question. A question its own article cannot answer is noise in training.

The retriever is deliberately not the filter: keeping only questions the base model already
retrieves would teach the fine-tuned model nothing it does not know.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mizan_core.logging import get_logger

log = get_logger(__name__)

GENERATOR_PROMPT = """\
Aşağıda Azərbaycan Respublikası Əmək Məcəlləsinin bir maddəsi var. Bu maddənin cavab verdiyi \
{n} müxtəlif sual yaz: işçi və ya işəgötürənin real həyatda verəcəyi suallar.

Qaydalar:
- Danışıq dilində yaz, maddənin sözlərini təkrarlama (məsələn, "xitam" əvəzinə "işdən çıxmaq").
- Suallar fərqli olsun: biri qayda, biri müddət və ya məbləğ, biri xüsusi hal (istisna).
- Maddənin nömrəsini çəkmə.
- Hər sual maddənin mətnindən birbaşa cavablandırıla bilməlidir.

Maddə:
{article}

Yalnız JSON qaytar: {{"questions": ["...", "..."]}}"""

JUDGE_PROMPT = """\
Suallar və bir hüquqi mətn verilib. Hər sual üçün: mətn bu suala birbaşa cavab verirmi? \
Cavab mətndə açıq yazılıbsa true, yalnız qismən və ya ümumiyyətlə yoxdursa false.

Suallar:
{questions}

Mətn:
{article}

Yalnız JSON qaytar, sualların sırası ilə: {{"answerable": [true, false, ...]}}"""


class _Questions(BaseModel):
    questions: list[str] = Field(default_factory=list)


class _Answerable(BaseModel):
    answerable: list[bool] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Pair:
    question: str
    article: str  # article key, "art:69"
    generator: str
    kept: bool  # passed the round-trip check


def _json(content: str) -> Any:
    start, end = content.find("{"), content.rfind("}")
    return json.loads(content[start : end + 1]) if start >= 0 and end > start else {}


async def _chat(
    client: Any, model: str, prompt: str, *, max_tokens: int, temperature: float
) -> str:
    for attempt in range(4):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_completion_tokens=max_tokens,
                **({"reasoning_effort": "low"} if "gpt-oss" in model else {}),
            )
            return str(response.choices[0].message.content or "")
        except Exception as exc:  # noqa: BLE001 - rate limits: back off
            wait = 20 * (attempt + 1)
            log.warning("synth_retry", model=model, wait_s=wait, error=str(exc)[:120])
            await asyncio.sleep(wait)
    raise RuntimeError(f"{model} kept failing")


async def generate(
    client: Any, model: str, article: str, *, n: int = 4, max_chars: int = 6000
) -> list[str]:
    content = await _chat(
        client,
        model,
        GENERATOR_PROMPT.format(n=n, article=article[:max_chars]),
        max_tokens=800,
        temperature=0.7,  # variety across the article's questions
    )
    try:
        questions = _Questions.model_validate(_json(content)).questions
    except ValueError:
        return []
    return [q.strip() for q in questions if 10 <= len(q.strip()) <= 300][:n]


async def answerable(
    client: Any, model: str, questions: Sequence[str], article: str, *, max_chars: int = 6000
) -> list[bool]:
    """One call per article: is each question answered by the article? (round-trip check)"""
    if not questions:
        return []
    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, start=1))
    content = await _chat(
        client,
        model,
        JUDGE_PROMPT.format(questions=numbered, article=article[:max_chars]),
        max_tokens=400,
        temperature=0.0,
    )
    try:
        verdicts = _Answerable.model_validate(_json(content)).answerable
    except ValueError:
        verdicts = []
    # A missing verdict is a rejection: unverified questions do not enter training.
    return [bool(verdicts[i]) if i < len(verdicts) else False for i in range(len(questions))]


def load_pairs(path: Path) -> list[Pair]:
    if not path.exists():
        return []
    return [
        Pair(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line
    ]


async def synthesise(
    client: Any,
    articles: Sequence[tuple[str, str]],
    out: Path,
    *,
    generator: str,
    judge: str,
    n: int = 4,
    pace_s: float = 0.0,
) -> list[Pair]:
    """Resumable: articles already in ``out`` are skipped."""
    pairs = load_pairs(out)
    done = {p.article for p in pairs}
    out.parent.mkdir(parents=True, exist_ok=True)
    for i, (key, text) in enumerate(articles, start=1):
        if key in done:
            continue
        questions = await generate(client, generator, text, n=n)
        verdicts = await answerable(client, judge, questions, text)
        new = [Pair(q, key, generator, ok) for q, ok in zip(questions, verdicts, strict=True)]
        with out.open("a", encoding="utf-8") as fh:
            for p in new:
                fh.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
        pairs += new
        log.info(
            "synth_progress",
            done=i,
            total=len(articles),
            article=key,
            kept=sum(p.kept for p in new),
        )
        if pace_s:
            await asyncio.sleep(pace_s)
    return pairs
