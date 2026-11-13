"""F12 evaluation: article references and query rewriting on everyday wording.

Runs the four variants of ``PROTOCOL.md`` on the held-out colloquial set and on v1, and writes
``report.md`` and ``results.json`` next to this file. Restatements come from the existing
rewrite model and are cached in ``rewrites_<set>.json``, so a rerun costs no LLM calls.

    uv run python evals/results/f12_colloquial/run.py
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from mizan_core.config import get_settings
from mizan_ingestion.chunking import Strategy
from mizan_retrieval.cli import LABOUR_CODE, _embedders, _qdrant, load_rewrites
from mizan_retrieval.evaluation import (
    METRICS,
    Question,
    RunResult,
    bootstrap,
    load_dataset,
)
from mizan_retrieval.search import Hit, Mode, SearchConfig, Searcher

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
SETS = {
    "colloquial": ROOT / "evals/datasets/colloquial/questions.jsonl",
    "v1": ROOT / "evals/datasets/v1/questions.jsonl",
}
BASE = SearchConfig(Mode.DENSE, dense_model="bge-m3", strategy=Strategy.ARTICLE)
VARIANTS = ["V0 baseline", "V1 refs", "V2 rewrite", "V3 multi-query"]
K = 10
RRF_K = 60
ROUNDS = 10_000
SEED = 0
REPORTED = ("Hit@5", "Recall@5", "MRR@10")


def restatement(expanded: str) -> str | None:
    """The rewrite alone: ``expand`` returns ``question + "\\n" + restatement``."""
    return expanded.split("\n", 1)[1] if "\n" in expanded else None


def rrf(*rankings: Sequence[Hit]) -> list[Hit]:
    scores: dict[str, float] = {}
    first: dict[str, Hit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            scores[hit.key] = scores.get(hit.key, 0.0) + 1.0 / (RRF_K + rank)
            first.setdefault(hit.key, hit)
    return [first[key] for key in sorted(scores, key=lambda key: -scores[key])]


async def run_set(
    searcher: Searcher, questions: list[Question], rewrites: dict[str, str]
) -> dict[str, RunResult]:
    results = {name: RunResult(name) for name in VARIANTS}
    for q in questions:
        if not q.relevant:
            continue

        async def ranked(query: str, config: SearchConfig, q: Question = q) -> list[Hit]:
            return await searcher.search(
                query, source_id=LABOUR_CODE, as_of=q.as_of, config=config, k=K
            )

        expanded = rewrites.get(q.id, q.question)
        alone = restatement(expanded)
        started = time.perf_counter()
        base = await ranked(q.question, BASE)
        runs = {
            "V0 baseline": base,
            "V1 refs": await ranked(q.question, replace(BASE, refs=True)),
            "V2 rewrite": await ranked(expanded, BASE),
            "V3 multi-query": rrf(base, await ranked(alone, BASE))[:K] if alone else base,
        }
        elapsed = (time.perf_counter() - started) * 1000
        for name, hits in runs.items():
            results[name].add(q, [h.article_key for h in hits], elapsed)
    return results


def compare(base: RunResult, other: RunResult, metric: str) -> dict[str, float]:
    ids = sorted(base.per_question)
    deltas = [other.per_question[i][metric] - base.per_question[i][metric] for i in ids]
    est = bootstrap(deltas, rounds=ROUNDS, seed=SEED)
    return {"delta": est.mean, "low": est.low, "high": est.high}


def rows(results: dict[str, RunResult]) -> list[str]:
    out = [
        "| Variant | " + " | ".join(REPORTED) + " | Δ Recall@5 vs V0 [95% CI] |",
        "|---" * 5 + "|",
    ]
    base = results["V0 baseline"]
    for name, result in results.items():
        summary = result.summary()
        cells = [f"{summary[m].mean:.3f}" for m in REPORTED]
        if name == "V0 baseline":
            delta = "-"
        else:
            d = compare(base, result, "Recall@5")
            delta = f"{d['delta']:+.3f} [{d['low']:+.3f}, {d['high']:+.3f}]"
        out.append(f"| {name} | " + " | ".join(cells) + f" | {delta} |")
    return out


def changed(base: RunResult, other: RunResult, questions: list[Question]) -> list[str]:
    text = {q.id: q.question for q in questions}
    out = []
    for qid in sorted(base.per_question):
        before = base.per_question[qid]["Hit@5"]
        after = other.per_question[qid]["Hit@5"]
        if before != after:
            sign = "fixed" if after > before else "broken"
            out.append(f"- {sign}: `{qid}` {text[qid]}")
    return out


async def main() -> None:
    settings = get_settings()
    client = _qdrant(settings)
    searcher = Searcher(client, _embedders(settings, ["bge-m3"]), None)
    report = [
        "# F12 results: everyday wording",
        "",
        f"- Generated: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC; protocol: `PROTOCOL.md`.",
        f"- Dense `bge-m3`, article chunks, top {K} chunks per query; metrics at article level.",
        f"- Δ: paired bootstrap over questions ({ROUNDS} resamples, seed {SEED}).",
        "",
    ]
    payload: dict[str, object] = {}
    try:
        for name, path in SETS.items():
            questions = load_dataset(path)
            rewrites = await load_rewrites(questions, HERE / f"rewrites_{name}.json")
            results = await run_set(searcher, questions, rewrites)
            answerable = [q for q in questions if q.relevant]
            report += [f"## {name} (n = {len(answerable)})", "", *rows(results), ""]
            for variant in VARIANTS[1:]:
                diff = changed(results["V0 baseline"], results[variant], questions)
                report += [f"**{variant}: Hit@5 changes vs V0** ({len(diff)})", "", *diff, ""]
            payload[name] = {
                variant: {
                    "summary": {m: r.summary()[m].mean for m in METRICS},
                    "vs_V0": {m: compare(results["V0 baseline"], r, m) for m in REPORTED},
                    "per_question": r.per_question,
                }
                for variant, r in results.items()
            }
            payload[f"{name}_rewritten"] = len(rewrites)
    finally:
        await client.close()
    (HERE / "report.md").write_text("\n".join(report), encoding="utf-8")
    (HERE / "results.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    asyncio.run(main())
