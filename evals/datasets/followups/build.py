"""Build the follow-up question set (F12).

Each item is a two-turn conversation: a v1 question (sampled with a fixed seed from the
`single` type) and a short follow-up that only makes sense after it ("Bəs uşağı 4
yaşındadırsa?"). The follow-ups are written by ``gemini-2.5-flash``, which sees the first
question and the text of its gold article and is told to ask something that article answers,
leaning on the first question instead of repeating it. Label: the same gold article.

    uv run python evals/datasets/followups/build.py
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

import httpx

from mizan_core.config import get_settings
from mizan_core.llm_clients import client_for

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
OUT = HERE / "conversations.jsonl"
MODEL = "gemini-2.5-flash"
SEED = 7
N = 20
API = "http://127.0.0.1:18000"

INSTRUCTIONS = """\
Sən Azərbaycanda işləyən adi insanların söhbətini yazırsan. Hər element üçün sənə istifadəçinin
ilk sualı və həmin sualın cavabının olduğu qanun maddəsinin mətni veriləcək. İstifadəçinin
ikinci, DAVAM sualını yaz:
- Davam sualı ilk suala söykənir və onsuz başa düşülmür: əvəzlik və ya buraxılmış hissə
  işlət ("Bəs ...?", "Onda ...?", "Bu halda ...?"). İlk sualın mövzusunu təkrarlama.
- Davam sualının cavabı verilən maddədədir, amma ilk sualın cavabından fərqli bir şeyi
  soruşur (başqa şərt, istisna, müddət, məbləğ, hal).
- Gündəlik dil, birinci şəxs, ən çoxu 12 söz, Azərbaycan dilində. Maddə nömrəsi yazma.
Yalnız JSON qaytar: {"items": [{"id": "<verilən id>", "follow_up": "<davam sualı>"}, ...]}"""


def _article_text(key: str) -> str:
    response = httpx.get(f"{API}/v1/articles/{key}", timeout=30)
    response.raise_for_status()
    text = str(response.json()["text"])
    return text.split("\n", 1)[1] if "\n" in text else text


async def main() -> None:
    if OUT.exists():
        raise SystemExit(f"{OUT} exists: the set is frozen")
    rows = [
        json.loads(line)
        for line in (ROOT / "evals/datasets/v1/questions.jsonl").read_text("utf-8").splitlines()
    ]
    pool = [r for r in rows if r["type"] == "single" and len(r["relevant"]) == 1]
    picked = random.Random(SEED).sample(pool, N)  # noqa: S311  sampling, not security
    items = [
        {"id": f"f{i + 1:03d}", "first": r, "text": _article_text(r["relevant"][0])[:4000]}
        for i, r in enumerate(picked)
    ]
    user = "\n\n".join(
        f"[{it['id']}]\nİLK SUAL: {it['first']['question']}\nMADDƏ:\n{it['text']}" for it in items
    )
    client = client_for("gemini", get_settings().llm)
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
                x["id"]: x["follow_up"].strip()
                for x in json.loads(response.choices[0].message.content)["items"]
            }
            if set(got) != {it["id"] for it in items}:
                raise ValueError(f"ids returned: {sorted(got)}")
            break
        except Exception as exc:  # noqa: BLE001  rate limits and malformed replies retry
            print(f"attempt {attempt + 1} failed: {exc!r}")
            await asyncio.sleep(20 * (attempt + 1))
    else:
        raise SystemExit("generation failed; nothing written")
    out = [
        {
            "id": it["id"],
            "first_id": it["first"]["id"],
            "first": it["first"]["question"],
            "follow_up": got[it["id"]],
            "relevant": it["first"]["relevant"],
        }
        for it in items
    ]
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), "utf-8")
    print(f"wrote {len(out)} conversations to {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
