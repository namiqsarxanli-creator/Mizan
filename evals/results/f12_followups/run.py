"""F12: does restating follow-up questions find the right article?

For each two-turn conversation in ``evals/datasets/followups``: the first question is answered
by the running API (as the question page does), then the follow-up is searched three ways with
the served search configuration:

- **as written**: the follow-up alone ("Bəs ona nə qədər tez-tez baxılır?");
- **concatenated**: first question + follow-up, no LLM (the cheap alternative);
- **condensed**: restated as a standalone question by the condenser that ``/v1/ask`` uses.

Hit@5 at article level against the first question's gold article. The decision this informs
was fixed before running: condensing stays on only if it beats "as written" (paired CI above 0)
and is not below "concatenated" in point estimate.

    uv run python evals/results/f12_followups/run.py   (needs the API on 127.0.0.1:18000)
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path

import groq
import httpx

from mizan_core.config import get_settings
from mizan_ingestion.chunking import Strategy
from mizan_retrieval.cli import LABOUR_CODE, _embedders, _qdrant
from mizan_retrieval.condense import GroqQuestionCondenser, Turn
from mizan_retrieval.evaluation import bootstrap, dedupe, hit_at
from mizan_retrieval.search import Mode, SearchConfig, Searcher

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
DATA = ROOT / "evals/datasets/followups/conversations.jsonl"
ANSWERS = HERE / "first_answers.json"
CONFIG = SearchConfig(Mode.DENSE, dense_model="bge-m3", strategy=Strategy.ARTICLE, refs=True)
API = "http://127.0.0.1:18000"
WAYS = ["as written", "concatenated", "condensed"]


async def first_answers(rows: list[dict[str, object]]) -> dict[str, str]:
    """Answers to the first questions, from the API, cached so reruns cost no LLM calls."""
    cached: dict[str, str] = json.loads(ANSWERS.read_text("utf-8")) if ANSWERS.exists() else {}
    async with httpx.AsyncClient(base_url=API, timeout=180) as http:
        for row in rows:
            rid = str(row["id"])
            if rid in cached:
                continue
            for attempt in range(4):
                response = await http.post("/v1/ask", json={"question": row["first"]})
                if response.status_code == 200:
                    cached[rid] = response.json()["answer"]
                    break
                await asyncio.sleep(30 * (attempt + 1))  # free-tier tokens per minute
            await asyncio.sleep(8)
    ANSWERS.write_text(json.dumps(cached, ensure_ascii=False, indent=1), "utf-8")
    return cached


async def main() -> None:
    rows = [json.loads(line) for line in DATA.read_text("utf-8").splitlines() if line.strip()]
    answers = await first_answers(rows)
    settings = get_settings()
    key = settings.llm.groq_api_key
    if key is None:
        raise SystemExit("needs MIZAN_LLM__GROQ_API_KEY")
    llm = groq.AsyncGroq(api_key=key.get_secret_value())
    condenser = GroqQuestionCondenser(llm, model=settings.llm.rewrite_model, timeout_s=30)
    client = _qdrant(settings)
    searcher = Searcher(client, _embedders(settings, ["bge-m3"]), None)
    today = datetime.now(UTC).date()
    scores: dict[str, list[float]] = {w: [] for w in WAYS}
    lines = []
    try:
        for row in rows:
            rid = str(row["id"])
            history = [Turn(str(row["first"]), answers.get(rid, ""))]
            condensed = (await condenser.condense(str(row["follow_up"]), history)).question
            queries = {
                "as written": str(row["follow_up"]),
                "concatenated": f"{row['first']} {row['follow_up']}",
                "condensed": condensed,
            }
            gold = frozenset(row["relevant"])  # type: ignore[arg-type]
            hits_by_way = {}
            for way, query in queries.items():
                hits = await searcher.search(
                    query, source_id=LABOUR_CODE, as_of=today, config=CONFIG, k=10
                )
                ranked = dedupe([h.article_key for h in hits])
                hits_by_way[way] = hit_at(ranked, gold, 5)
                scores[way].append(hits_by_way[way])
            marks = " ".join("✓" if hits_by_way[w] else "✗" for w in WAYS)
            lines.append(f"| `{rid}` | {row['follow_up']} | {condensed} | {marks} |")
            await asyncio.sleep(2)
    finally:
        await client.close()
        await llm.close()

    def delta(a: str, b: str) -> str:
        est = bootstrap([x - y for x, y in zip(scores[a], scores[b], strict=True)], rounds=10_000)
        return f"{est.mean:+.2f} [{est.low:+.2f}, {est.high:+.2f}]"

    report = [
        "# F12 results: follow-up questions",
        "",
        f"- Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC on {date.today()}; "
        f"{len(rows)} conversations; served search (dense bge-m3 + refs), Hit@5.",
        "",
        "| Way | Hit@5 |",
        "|---|---|",
        *[f"| {w} | {bootstrap(scores[w])} |" for w in WAYS],
        "",
        f"- condensed − as written: {delta('condensed', 'as written')}",
        f"- condensed − concatenated: {delta('condensed', 'concatenated')}",
        "",
        "| id | follow-up | condensed | as written / concatenated / condensed |",
        "|---|---|---|---|",
        *lines,
    ]
    (HERE / "report.md").write_text("\n".join(report) + "\n", "utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    asyncio.run(main())
