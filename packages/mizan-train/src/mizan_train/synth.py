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
    """The answer's JSON object, after any visible reasoning (``<thought>``, ``<think>``)."""
    for closing in ("</thought>", "</think>"):
        if closing in content:
            content = content.rsplit(closing, 1)[1]
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        return json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return {}


def _reasoning(model: str) -> dict[str, str]:
    """Writing or checking questions needs no long reasoning: keep the budget for the answer."""
    if "gpt-oss" in model:
        return {"reasoning_effort": "low"}
    if model.startswith("gemini"):
        return {"reasoning_effort": "none"}
    return {}


class QuotaExhaustedError(RuntimeError):
    """A provider's *daily* quota is spent: waiting minutes will not help, switch models."""


def _daily_quota(exc: Exception) -> bool:
    # The quota details sit deep in the provider's error body, past any truncated message.
    response = getattr(exc, "response", None)
    text = f"{exc} {getattr(response, 'text', '')}".lower()
    return "429" in text and ("per day" in text or "perday" in text or "tokens per day" in text)


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
                **_reasoning(model),
            )
            return str(response.choices[0].message.content or "")
        except Exception as exc:
            if _daily_quota(exc):
                raise QuotaExhaustedError(f"{model}: daily quota exhausted") from exc
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
    verdicts: list[bool] = []
    for _ in range(3):  # a truncated or malformed reply is asked again, not read as "no"
        content = await _chat(
            client,
            model,
            JUDGE_PROMPT.format(questions=numbered, article=article[:max_chars]),
            max_tokens=4000,  # room for a model that thinks aloud before the JSON
            temperature=0.0,
        )
        try:
            verdicts = _Answerable.model_validate(_json(content)).answerable
        except ValueError:
            verdicts = []
        if len(verdicts) == len(questions):
            break
        log.warning("judge_incomplete", model=model, got=len(verdicts), want=len(questions))
    # A still-missing verdict is a rejection: unverified questions do not enter training.
    return [bool(verdicts[i]) if i < len(verdicts) else False for i in range(len(questions))]


BATCH_GENERATOR_PROMPT = """\
Aşağıda Azərbaycan Respublikası Əmək Məcəlləsinin {k} maddəsi var, hər biri öz id-si ilə. \
Hər maddə üçün onun cavab verdiyi {n} müxtəlif sual yaz: işçi və ya işəgötürənin real \
həyatda verəcəyi suallar.

Qaydalar:
- Danışıq dilində, düzgün Azərbaycan dilində yaz; maddənin sözlərini təkrarlama.
- Suallar fərqli olsun: biri qayda, biri müddət və ya məbləğ, biri xüsusi hal (istisna).
- Maddənin nömrəsini çəkmə. Hər sual öz maddəsinin mətnindən cavablandırıla bilməlidir.

{articles}

Yalnız JSON qaytar: {{"items": [{{"id": "A1", "questions": ["...", "..."]}}]}}"""

BATCH_JUDGE_PROMPT = """\
Hər bölmədə bir hüquqi mətn və suallar var. Hər sual üçün: mətn bu suala birbaşa cavab \
verirmi? Cavab mətndə açıq yazılıbsa true, yalnız qismən və ya ümumiyyətlə yoxdursa false.

{items}

Yalnız JSON qaytar, hər bölmə üçün sualların sırası ilə: \
{{"items": [{{"id": "A1", "answerable": [true, false]}}]}}"""


class _BatchItem(BaseModel):
    id: str
    questions: list[str] = Field(default_factory=list)
    answerable: list[bool] = Field(default_factory=list)


class _Batch(BaseModel):
    items: list[_BatchItem] = Field(default_factory=list)


def _batch_items(content: str) -> dict[str, _BatchItem]:
    try:
        return {i.id: i for i in _Batch.model_validate(_json(content)).items}
    except ValueError:
        return {}


async def generate_batch(
    client: Any,
    model: str,
    articles: Sequence[tuple[str, str]],
    *,
    n: int = 4,
    max_chars: int = 3000,
) -> dict[str, list[str]]:
    """Questions for several articles in one request: free tiers count requests, not tokens."""
    ids = {f"A{i}": key for i, (key, _) in enumerate(articles, start=1)}
    blocks = "\n\n".join(
        f"[A{i}]\n{text[:max_chars]}" for i, (_, text) in enumerate(articles, start=1)
    )
    content = await _chat(
        client,
        model,
        BATCH_GENERATOR_PROMPT.format(k=len(articles), n=n, articles=blocks),
        max_tokens=8000,
        temperature=0.7,
    )
    out: dict[str, list[str]] = {}
    for item_id, item in _batch_items(content).items():
        if item_id in ids:
            qs = [q.strip() for q in item.questions if 10 <= len(q.strip()) <= 300][:n]
            if qs:
                out[ids[item_id]] = qs
    return out


async def answerable_batch(
    client: Any,
    model: str,
    items: Sequence[tuple[str, str, list[str]]],
    *,
    max_chars: int = 3000,
) -> dict[str, list[bool]]:
    """Round-trip verdicts for several (article, text, questions) in one request.

    An article whose verdict list does not match its questions gets no entry: it is redone
    on the next run rather than read as rejected.
    """
    ids = {f"A{i}": (key, qs) for i, (key, _, qs) in enumerate(items, start=1)}
    blocks = "\n\n".join(
        f"[A{i}]\nMətn:\n{text[:max_chars]}\nSuallar:\n"
        + "\n".join(f"{j}. {q}" for j, q in enumerate(qs, start=1))
        for i, (_, text, qs) in enumerate(items, start=1)
    )
    content = await _chat(
        client, model, BATCH_JUDGE_PROMPT.format(items=blocks), max_tokens=8000, temperature=0.0
    )
    out: dict[str, list[bool]] = {}
    for item_id, item in _batch_items(content).items():
        if item_id in ids and len(item.answerable) == len(ids[item_id][1]):
            out[ids[item_id][0]] = list(item.answerable)
    return out


def load_pairs(path: Path) -> list[Pair]:
    if not path.exists():
        return []
    return [
        Pair(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line
    ]


async def synthesise(
    generators: Sequence[tuple[Any, str]],
    judge: tuple[Any, str],
    articles: Sequence[tuple[str, str]],
    out: Path,
    *,
    n: int = 4,
    batch: int = 1,
    pace_s: float = 0.0,
) -> list[Pair]:
    """Resumable: articles already in ``out`` are skipped.

    ``generators`` are (client, model) pairs used in order: when one model's daily free quota
    runs out, the next takes over. Mixing generators also varies the questions' style.
    ``judge`` is the (client, model) of the round-trip check, another model family.
    ``batch`` articles go into one request each for generation and for judging.
    """
    pairs = load_pairs(out)
    done = {p.article for p in pairs}
    todo = [(k, t) for k, t in articles if k not in done]
    out.parent.mkdir(parents=True, exist_ok=True)
    judge_client, judge_model = judge
    current = 0
    for start in range(0, len(todo), batch):
        chunk = todo[start : start + batch]
        while True:
            if current >= len(generators):
                log.warning("synth_quota_spent", done=len(done), total=len(articles))
                return pairs
            gen_client, gen_model = generators[current]
            try:
                if batch == 1:
                    key, text = chunk[0]
                    questions = {key: await generate(gen_client, gen_model, text, n=n)}
                else:
                    questions = await generate_batch(gen_client, gen_model, chunk, n=n)
                break
            except QuotaExhaustedError:
                log.warning("synth_generator_switch", exhausted=gen_model)
                current += 1
        texts = dict(chunk)
        try:
            if batch == 1:
                verdicts = {
                    k: await answerable(judge_client, judge_model, qs, texts[k])
                    for k, qs in questions.items()
                }
            else:
                verdicts = await answerable_batch(
                    judge_client,
                    judge_model,
                    [(k, texts[k], qs) for k, qs in questions.items()],
                )
        except QuotaExhaustedError:
            log.warning("synth_judge_quota_spent", done=len(done), total=len(articles))
            return pairs
        new = [
            Pair(q, key, gen_model, ok)
            for key, qs in questions.items()
            if key in verdicts
            for q, ok in zip(qs, verdicts[key], strict=True)
        ]
        with out.open("a", encoding="utf-8") as fh:
            for p in new:
                fh.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
        pairs += new
        done |= {p.article for p in new}
        log.info(
            "synth_progress",
            done=len(done),
            total=len(articles),
            generator=gen_model,
            kept=sum(p.kept for p in new),
            missing=len(chunk) - len({p.article for p in new}),
        )
        if pace_s:
            await asyncio.sleep(pace_s)
    return pairs
