"""F12 amendment 1, step 2: answers with and without focused context.

Questions: every one whose packed context differs between the two modes (``context.json``),
sampled to 30 (seed 0), plus every question whose gold article is lost as served. Each is
answered by the served answer model in both modes, one right after the other, and judged by
the F3 judge against the gold provisions. Records go to ``answers/<mode>/records.jsonl``;
reruns resume, so a free-tier daily limit only delays the run.

    uv run python evals/results/f12_colloquial/answers.py
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import groq

from mizan_core.config import get_settings
from mizan_evals.cli import _gold_texts
from mizan_evals.judge import DEFAULT_JUDGE_MODEL, GroqJudge
from mizan_evals.run import load_records, run_generation_eval
from mizan_ingestion.chunking import Strategy
from mizan_retrieval.answer import GroqAnswerer
from mizan_retrieval.cli import LABOUR_CODE, _embedders, _qdrant
from mizan_retrieval.evaluation import Question, bootstrap, load_dataset
from mizan_retrieval.search import Hit, Mode, SearchConfig, Searcher, expand_to_articles

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
SETS = [
    ROOT / "evals/datasets/colloquial/questions.jsonl",
    ROOT / "evals/datasets/v1/questions.jsonl",
]
CONFIG = SearchConfig(Mode.DENSE, dense_model="bge-m3", strategy=Strategy.ARTICLE, refs=True)
MODES = {"as_served": 0, "F2500": 2500}
SAMPLE = 30
PACE_S = 20.0


def chosen() -> list[str]:
    ctx = json.loads((HERE / "context.json").read_text("utf-8"))
    differ = sorted(ctx["colloquial/differ"] + ctx["v1/differ"])
    lost = ctx["colloquial/as served"]["lost"] + ctx["v1/as served"]["lost"]
    sample = random.Random(0).sample(differ, min(SAMPLE, len(differ)))  # noqa: S311
    return list(dict.fromkeys(sorted(sample) + sorted(lost)))


async def main() -> None:
    settings = get_settings()
    key = settings.llm.groq_api_key
    if key is None:
        raise SystemExit("needs MIZAN_LLM__GROQ_API_KEY")
    questions = {q.id: q for path in SETS for q in load_dataset(path)}
    ids = chosen()
    llm = groq.AsyncGroq(api_key=key.get_secret_value())
    answerer = GroqAnswerer(
        llm,
        model=settings.llm.groq_model,
        reasoning_effort=settings.llm.effort,
        max_context_chars=settings.llm.max_context_chars,
    )
    judge = GroqJudge(llm, model=DEFAULT_JUDGE_MODEL)
    client = _qdrant(settings)
    searcher = Searcher(client, _embedders(settings, ["bge-m3"]), None)
    try:
        for qid in ids:
            for mode, over in MODES.items():

                async def expand(hits: Sequence[Hit], on: date, over: int = over) -> list[Hit]:
                    return await expand_to_articles(
                        client, hits, source_id=LABOUR_CODE, as_of=on, focus_over=over
                    )

                await run_generation_eval(
                    [questions[qid]],
                    searcher=searcher,
                    config=CONFIG,
                    expand=expand,
                    answerer=answerer,
                    judge=judge,
                    gold_text=_gold_texts,
                    source_id=LABOUR_CODE,
                    out=HERE / "answers" / mode / "records.jsonl",
                )
                await asyncio.sleep(PACE_S)
    finally:
        await client.close()
        await llm.close()
    summarise(ids, questions)


def summarise(ids: Sequence[str], questions: dict[str, Question]) -> None:
    runs = {m: load_records(HERE / "answers" / m / "records.jsonl") for m in MODES}
    both = [
        i
        for i in ids
        if all(i in r and r[i].error is None and r[i].correct is not None for r in runs.values())
    ]
    lines = [
        "# F12 amendment 1, step 2: answers with and without focused context",
        "",
        f"{len(both)} of {len(ids)} chosen questions answered and judged in both modes.",
        "",
        "| Mode | judged correct | cites a gold article |",
        "|---|---|---|",
    ]
    for mode, r in runs.items():
        correct = bootstrap([float(bool(r[i].correct)) for i in both])
        cites = bootstrap([float(bool(r[i].cites_gold)) for i in both])
        lines.append(f"| {mode} | {correct} | {cites} |")
    delta = bootstrap(
        [
            float(bool(runs["F2500"][i].correct)) - float(bool(runs["as_served"][i].correct))
            for i in both
        ],
        rounds=10_000,
    )
    lines += [
        "",
        f"F2500 minus as served, judged correct: {delta}",
        "",
        "| id | question | as served | F2500 |",
        "|---|---|---|---|",
    ]
    for i in both:
        a, f = runs["as_served"][i], runs["F2500"][i]
        if a.correct != f.correct:
            lines.append(
                f"| `{i}` | {questions[i].question} | {'✓' if a.correct else '✗'} | "
                f"{'✓' if f.correct else '✗'} |"
            )
    (HERE / "answers.md").write_text("\n".join(lines) + "\n", "utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
