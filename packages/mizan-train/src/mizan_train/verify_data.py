"""Verification claims with labels known by construction (F8 week 18).

The verifier decides whether a source text supports a legal claim. Gold labels for that are
expensive to write by hand, so the claims are *constructed* to have a label. An LLM rewrites a
rule of the article in four ways:

- ``restated``: the rule in other words, so ``supported``;
- ``altered``: one detail changed (a number, a period, a party, a condition), so ``contradicted``;
- ``absent``: a plausible labour-law claim this article does not address, so ``not_enough_info``;
- ``extended``: the true rule plus a requirement the article does not state, so
  ``not_enough_info``. This is F5's failure mode: a supported claim with an extra clause.

A judge from another model family then labels each claim from the source alone. Claims where
it disagrees with the intended label are dropped, because the construction did not produce the
claim it was asked for, or the claim is ambiguous.
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
from mizan_train.dataset import article_split
from mizan_train.synth import QuotaExhaustedError, _chat, _json

log = get_logger(__name__)

LABELS = ("supported", "contradicted", "not_enough_info")
KINDS = {
    "restated": "supported",
    "altered": "contradicted",
    "absent": "not_enough_info",
    "extended": "not_enough_info",
}

GENERATOR_PROMPT = """\
Aşağıda Azərbaycan Respublikası Əmək Məcəlləsinin {k} maddəsi var, hər biri öz id-si ilə. \
Hər maddə üçün bir hüquqi iddia cümləsinin dörd variantını yaz:

- "restated": maddənin bir qaydasını başqa sözlərlə düzgün ifadə edən iddia;
- "altered": eyni qayda, amma bir detalı dəyişdirilib (rəqəm, müddət, tərəf və ya şərt) ki, \
maddə ona zidd olsun;
- "absent": əmək hüququna aid inandırıcı iddia, amma bu maddə bu barədə heç nə demir;
- "extended": "restated" iddiası + maddədə olmayan əlavə tələb və ya şərt (məsələn, staj, \
yazılı ərizə, məbləğ həddi).

Qaydalar: hər iddia bir cümlədir, düzgün Azərbaycan dilində; maddənin nömrəsini çəkmə; \
maddənin cümlələrini hərfi köçürmə.

{articles}

Yalnız JSON qaytar: {{"items": [{{"id": "A1", "restated": "...", "altered": "...", \
"absent": "...", "extended": "..."}}]}}"""

JUDGE_PROMPT = """\
Sən hüquqi iddiaları mənbə ilə yoxlayan yoxlayıcısan. Hər bölmədə bir mənbə mətni və \
nömrələnmiş iddialar var. Hər iddia üçün yalnız həmin mənbəyə əsasən qərar ver:
- "supported": mənbə iddianın hamısını birbaşa təsdiqləyir;
- "contradicted": mənbə iddianın əksini deyir (başqa müddət, başqa şərt və s.);
- "not_enough_info": mənbədən bu nəticə çıxmır, və ya iddiada mənbədə olmayan əlavə tələb var.
Ümumi biliyindən istifadə etmə.

{items}

Yalnız JSON qaytar, hər bölmə üçün iddiaların sırası ilə: \
{{"items": [{{"id": "A1", "labels": ["supported", "contradicted"]}}]}}"""


class _Generated(BaseModel):
    id: str
    restated: str = ""
    altered: str = ""
    absent: str = ""
    extended: str = ""


class _Judged(BaseModel):
    id: str
    labels: list[str] = Field(default_factory=list)


class _Items(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class VerifyClaim:
    article: str  # article key, "art:69"
    kind: str  # restated / altered / absent / extended
    claim: str
    label: str  # intended, from the kind
    split: str  # train / val / test, by article
    generator: str
    judge_label: str
    kept: bool  # judge agrees with the intended label


def _items(content: str) -> list[dict[str, Any]]:
    try:
        return _Items.model_validate(_json(content)).items
    except ValueError:
        return []


async def generate_claims(
    client: Any, model: str, articles: Sequence[tuple[str, str]], *, max_chars: int = 2500
) -> dict[str, dict[str, str]]:
    """Four constructed claims per article, several articles per request."""
    ids = {f"A{i}": key for i, (key, _) in enumerate(articles, start=1)}
    blocks = "\n\n".join(
        f"[A{i}]\n{text[:max_chars]}" for i, (_, text) in enumerate(articles, start=1)
    )
    content = await _chat(
        client,
        model,
        GENERATOR_PROMPT.format(k=len(articles), articles=blocks),
        max_tokens=8000,
        temperature=0.7,
    )
    out: dict[str, dict[str, str]] = {}
    for raw in _items(content):
        try:
            item = _Generated.model_validate(raw)
        except ValueError:
            continue
        claims = {k: getattr(item, k).strip() for k in KINDS}
        if item.id in ids and all(10 <= len(c) <= 400 for c in claims.values()):
            out[ids[item.id]] = claims
    return out


async def judge_claims(
    client: Any,
    model: str,
    items: Sequence[tuple[str, str, list[str]]],
    *,
    max_chars: int = 2500,
) -> dict[str, list[str]]:
    """Labels for several (article, source text, claims) in one request.

    An article whose label list does not match its claims gets no entry: it is redone on the
    next run rather than read as a disagreement.
    """
    ids = {f"A{i}": (key, claims) for i, (key, _, claims) in enumerate(items, start=1)}
    blocks = "\n\n".join(
        f"[A{i}]\nMənbə:\n{text[:max_chars]}\nİddialar:\n"
        + "\n".join(f"{j}. {c}" for j, c in enumerate(claims, start=1))
        for i, (_, text, claims) in enumerate(items, start=1)
    )
    content = await _chat(
        client, model, JUDGE_PROMPT.format(items=blocks), max_tokens=4000, temperature=0.0
    )
    out: dict[str, list[str]] = {}
    for raw in _items(content):
        try:
            item = _Judged.model_validate(raw)
        except ValueError:
            continue
        labels = [label.strip().lower() for label in item.labels]
        if item.id in ids and len(labels) == len(ids[item.id][1]) and set(labels) <= set(LABELS):
            out[ids[item.id][0]] = labels
    return out


def load_claims(path: Path) -> list[VerifyClaim]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [VerifyClaim(**json.loads(line)) for line in lines if line]


async def synthesise_claims(
    generators: Sequence[tuple[Any, str]],
    judge: tuple[Any, str],
    articles: Sequence[tuple[str, str]],
    out: Path,
    *,
    batch: int = 8,
    pace_s: float = 0.0,
) -> list[VerifyClaim]:
    """Resumable: articles already in ``out`` are skipped. Generators rotate on daily quotas."""
    claims = load_claims(out)
    done = {c.article for c in claims}
    todo = [(k, t) for k, t in articles if k not in done]
    out.parent.mkdir(parents=True, exist_ok=True)
    judge_client, judge_model = judge
    current = 0
    for start in range(0, len(todo), batch):
        chunk = todo[start : start + batch]
        while True:
            if current >= len(generators):
                log.warning("verify_synth_quota_spent", done=len(done), total=len(articles))
                return claims
            gen_client, gen_model = generators[current]
            try:
                generated = await generate_claims(gen_client, gen_model, chunk)
                break
            except QuotaExhaustedError:
                log.warning("verify_synth_generator_switch", exhausted=gen_model)
                current += 1
        texts = dict(chunk)
        try:
            labels = await judge_claims(
                judge_client,
                judge_model,
                [(k, texts[k], list(c.values())) for k, c in generated.items()],
            )
        except QuotaExhaustedError:
            log.warning("verify_synth_judge_quota_spent", done=len(done), total=len(articles))
            return claims
        new = [
            VerifyClaim(
                article=key,
                kind=kind,
                claim=text,
                label=KINDS[kind],
                split=article_split(key),
                generator=gen_model,
                judge_label=judged,
                kept=judged == KINDS[kind],
            )
            for key, by_kind in generated.items()
            if key in labels
            for (kind, text), judged in zip(by_kind.items(), labels[key], strict=True)
        ]
        with out.open("a", encoding="utf-8") as fh:
            for c in new:
                fh.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
        claims += new
        done |= {c.article for c in new}
        log.info(
            "verify_synth_progress",
            done=len(done),
            total=len(articles),
            generator=gen_model,
            kept=sum(c.kept for c in new),
            claims=len(new),
        )
        if pace_s:
            await asyncio.sleep(pace_s)
    return claims
