"""Build the held-out colloquial retrieval set (F12).

The v1 questions were written by the same model family that later tuned the system, and every
retrieval decision since F2 was made on them. This set is written by a model from another
family (``gemini-2.5-flash``), from provisions sampled at random, and frozen before any of the
F12 variants is run on it.

Each question is generated from one provision of the Labour Code as in force today, in the
voice of a worker or employer, without the provision's own legal terms: the vocabulary gap is
what the set measures. The provision's article is the label. Another article may also answer a
question, so recall against this label is a lower bound.

    uv run python evals/datasets/colloquial/build.py
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import date
from pathlib import Path

import httpx

from mizan_core.config import get_settings
from mizan_core.llm_clients import client_for

HERE = Path(__file__).parent
OUT = HERE / "questions.jsonl"
MODEL = "gemini-2.5-flash"
SEED = 2026
N_ARTICLES = 60
BATCH = 10
QDRANT = "http://127.0.0.1:16333"
COLLECTION = "legal_article"
SOURCE_ID = 46943

INSTRUCTIONS = """\
Sən Azərbaycanda işləyən adi insanların suallarını yazırsan. Sənə Azərbaycan Respublikasının
Əmək Məcəlləsindən bir neçə müddəa veriləcək. Hər müddəa üçün bir sual yaz:

- Sualı işçi və ya işəgötürən özü verir: gündəlik, danışıq dilində, birinci şəxsdə, öz
  vəziyyətini təsvir edərək (məsələn "Müdirim deyir ki...", "Məni ... edə bilərlərmi?").
- Müddəanın öz hüquqi terminlərini və ifadələrini İŞLƏTMƏ: adi insanın deyəcəyi sözlərlə yaz
  (məsələn "əmək müqaviləsinə xitam" yox, "işdən çıxarmaq"; "əmək haqqı" yox, "maaş").
- Maddə nömrəsini, "Əmək Məcəlləsi" sözünü və qanunun adını yazma.
- Sualın cavabı həmin müddəada olmalıdır.
- Bir cümlə, ən çoxu 25 söz, Azərbaycan dilində.

Yalnız JSON qaytar: {"questions": [{"id": "<verilən id>", "question": "<sual>"}, ...]}"""


def _in_force_today() -> list[dict[str, object]]:
    today = date.today().toordinal()  # noqa: DTZ011  the set is built once, on the day it is frozen
    flt = {
        "must": [
            {"key": "source_id", "match": {"value": SOURCE_ID}},
            {"key": "valid_from", "range": {"lte": today}},
            {"key": "valid_to", "range": {"gt": today}},
        ]
    }
    points: list[dict[str, object]] = []
    offset = None
    with httpx.Client(base_url=QDRANT, timeout=30) as http:
        while True:
            body: dict[str, object] = {"filter": flt, "limit": 256, "with_payload": True}
            if offset is not None:
                body["offset"] = offset
            data = http.post(f"/collections/{COLLECTION}/points/scroll", json=body).json()
            points += [p["payload"] for p in data["result"]["points"]]
            offset = data["result"]["next_page_offset"]
            if offset is None:
                return points


def _body(text: str) -> str:
    """The provision without its breadcrumb (code > section > chapter > article title)."""
    return text.split("\n", 1)[1].strip() if "\n" in text else text


def _sample() -> list[dict[str, str]]:
    by_article: dict[str, list[dict[str, object]]] = {}
    for p in _in_force_today():
        body = _body(str(p["text"]))
        if len(body) < 150 or re.search(r"ləğv edilmişdir|çıxarılmışdır", body[:200]):
            continue
        by_article.setdefault(str(p["article_key"]), []).append(p)
    rng = random.Random(SEED)  # noqa: S311  sampling, not security
    articles = sorted(by_article)
    rng.shuffle(articles)
    picked = []
    for i, article in enumerate(sorted(articles[:N_ARTICLES], key=_order)):
        part = rng.choice(sorted(by_article[article], key=lambda p: str(p["key"])))
        picked.append(
            {
                "id": f"c{i + 1:03d}",
                "article": article,
                "key": str(part["key"]),
                "text": _body(str(part["text"])),
            }
        )
    return picked


def _order(key: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", key))


async def main() -> None:
    if OUT.exists():
        raise SystemExit(f"{OUT} exists: the set is frozen")
    sample = _sample()
    client = client_for("gemini", get_settings().llm)
    rows: list[dict[str, object]] = []
    for start in range(0, len(sample), BATCH):
        batch = sample[start : start + BATCH]
        user = "\n\n".join(f"[{s['id']}]\n{s['text']}" for s in batch)
        for attempt in range(5):
            try:
                response = await client.create(
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": INSTRUCTIONS},
                        {"role": "user", "content": user},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.7,
                    max_completion_tokens=8192,
                    reasoning_effort="low",
                )
                got = {
                    q["id"]: q["question"].strip()
                    for q in json.loads(response.choices[0].message.content)["questions"]
                }
                if set(got) != {s["id"] for s in batch}:
                    raise ValueError(f"ids returned: {sorted(got)}")
                break
            except Exception as exc:  # noqa: BLE001  rate limits and malformed replies retry
                print(f"batch {start}: attempt {attempt + 1} failed: {exc!r}")
                await asyncio.sleep(20 * (attempt + 1))
        else:
            raise SystemExit("generation failed; nothing written")
        for s in batch:
            rows.append(
                {
                    "id": s["id"],
                    "question": got[s["id"]],
                    "relevant": [s["article"]],
                    "type": "colloquial",
                    "source_provision": s["key"],
                }
            )
        print(f"{len(rows)}/{len(sample)}")
    OUT.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    print(f"wrote {len(rows)} questions to {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
