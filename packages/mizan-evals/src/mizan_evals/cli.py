"""Generation eval command line.

mizan-eval run    [--sample 60] [--pace 45]   retrieve -> answer -> judge (resumable)
mizan-eval report                             metrics with 95% CIs -> report.md / summary.json
mizan-eval label  --labeller NAME             blind human labelling (resumable)
mizan-eval kappa                              judge vs human agreement (Cohen's kappa)
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import groq
from neo4j import AsyncGraphDatabase
from qdrant_client import AsyncQdrantClient

from mizan_core.config import get_settings
from mizan_core.logging import configure_logging
from mizan_evals.judge import DEFAULT_JUDGE_MODEL, GroqJudge
from mizan_evals.labels import agreement, label_session, load_labels
from mizan_evals.run import Record, load_records, run_generation_eval, stratified_sample, summarise
from mizan_graph.decisions import attach_decisions, decision_hit
from mizan_graph.search import SERVING_POLICY, GraphSearcher
from mizan_graph.store import Neo4jGraph
from mizan_ingestion.db import repository as repo
from mizan_ingestion.db.session import create_engine, session_factory
from mizan_ingestion.sources.eqanun import SOURCE
from mizan_retrieval.answer import GroqAnswerer
from mizan_retrieval.cli import serving_variant
from mizan_retrieval.embeddings import MODELS, Embedder, EmbeddingCache
from mizan_retrieval.evaluation import Question, load_dataset
from mizan_retrieval.search import Hit, SearchBackend, Searcher, expand_to_articles

DEFAULT_DATASET = Path("evals/datasets/v1/questions.jsonl")
DEFAULT_OUT = Path("evals/results/f3_generation")
KAPPA_TARGET = 0.6


async def _gold_texts(keys: Sequence[str], on: date) -> str:
    """The gold articles as they stood on ``on``, rendered for a judge or a human."""
    if not keys:
        return ""
    settings = get_settings()
    engine = create_engine(settings.postgres)
    parts = []
    try:
        async with session_factory(engine)() as session:
            for key in keys:
                _, rows = await repo.provisions_as_of(
                    session,
                    source=SOURCE,
                    source_id=settings.retrieval.source_id,
                    on=on,
                    key_prefix=key,
                )
                lines = []
                for row in rows:
                    label = f"[{row.number}] " if row.number and row.kind != "article" else ""
                    head = (
                        f"Maddə {row.number}. {row.content.heading}"
                        if row.kind == "article"
                        else ""
                    )
                    body = " ".join(x for x in (head, row.content.text) if x)
                    if body:
                        lines.append(f"{label}{body}")
                parts.append("\n".join(lines) or f"{key}: (not in force on {on})")
    finally:
        await engine.dispose()
    return "\n\n".join(parts)


def _expected(dataset: Path) -> dict[str, str]:
    """Optional per-question "expected" conclusions (the court-decision set has them)."""
    rows = (json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line)
    return {row["id"]: row["expected"] for row in rows if row.get("expected")}


async def _run(
    dataset: Path, out: Path, sample: int, pace_s: float, *, graph: bool, decisions: bool
) -> int:
    settings = get_settings()
    if settings.llm.groq_api_key is None:
        print("generation eval needs MIZAN_LLM__GROQ_API_KEY", file=sys.stderr)
        return 2
    questions = load_dataset(dataset)
    if sample:
        questions = stratified_sample(questions, sample)
    expected = _expected(dataset)
    config = serving_variant()
    key = settings.qdrant.api_key.get_secret_value() if settings.qdrant.api_key else None
    qdrant = AsyncQdrantClient(url=settings.qdrant.url, api_key=key, timeout=120)
    cache = EmbeddingCache(Path(settings.retrieval.embeddings_cache))
    client = groq.AsyncGroq(api_key=settings.llm.groq_api_key.get_secret_value())
    driver = AsyncGraphDatabase.driver(
        settings.neo4j.uri,
        auth=(settings.neo4j.user, settings.neo4j.password.get_secret_value()),
    )
    store = Neo4jGraph(driver)
    try:
        searcher: SearchBackend = Searcher(
            qdrant, {config.dense_model: Embedder(MODELS[config.dense_model], cache)}
        )
        if graph:
            searcher = GraphSearcher(searcher, store, SERVING_POLICY)
        source_id = settings.retrieval.source_id

        async def expand(hits: Sequence[Hit], on: date) -> list[Hit]:
            articles = await expand_to_articles(qdrant, hits, source_id=source_id, as_of=on)
            if not decisions:
                return articles
            return await attach_decisions(articles, store, source_id=source_id, as_of=on)

        async def extra_gold(q: Question) -> str:
            # The judge sees the same gold with and without decisions in the context.
            if q.type != "court_decision":
                return ""
            found = await store.decisions_for(source_id, sorted(q.relevant), as_of=q.as_of)
            parts = [decision_hit(d).text for d in found]
            if q.id in expected:
                parts.append(f"Expected conclusion: {expected[q.id]}")
            return "\n\n".join(parts)

        records = await run_generation_eval(
            questions,
            searcher=searcher,
            config=config,
            expand=expand,
            answerer=GroqAnswerer(
                client,
                model=settings.llm.groq_model,
                reasoning_effort=settings.llm.effort,
                max_context_chars=settings.llm.max_context_chars,
            ),
            judge=GroqJudge(client, model=DEFAULT_JUDGE_MODEL),
            gold_text=_gold_texts,
            source_id=source_id,
            out=out / "records.jsonl",
            pace_s=pace_s,
            extra_gold=extra_gold,
        )
    finally:
        await driver.close()
        await client.close()
        await qdrant.close()
        cache.close()
    chosen = {q.id for q in questions}
    _report(out, [r for r in records.values() if r.id in chosen])
    return 0


def _report(out: Path, records: Sequence[Record]) -> dict[str, object]:
    summary = summarise(records)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")

    def fmt(m: object) -> str:
        if not isinstance(m, dict) or not m.get("n"):
            return "n/a"
        return f"{m['mean']:.3f} [{m['low']:.2f}, {m['high']:.2f}] (n={m['n']})"

    lines = [
        "# Generation evaluation",
        "",
        f"Questions: {summary['questions']} (failed: {summary['failed']}). "
        "Judge-based numbers are uncalibrated until `mizan-eval kappa` reaches the target.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Answer correct (all) | {fmt(summary['answer_correct'])} |",
        f"| Answer correct (in scope) | {fmt(summary['answer_correct_in_scope'])} |",
        f"| Abstention on out-of-scope | {fmt(summary['abstention_accuracy'])} |",
        f"| Cites a gold article | {fmt(summary['cites_gold'])} |",
        f"| Citation valid (verbatim) | {summary['citation_valid']} |",
        f"| p50 latency (ms) | {summary['p50_latency_ms']} |",
        "",
        "## Answer correct by question type",
        "",
        "| Type | Correct |",
        "|---|---|",
        *(f"| {t} | {fmt(m)} |" for t, m in summary["by_type"].items()),
        "",
        "## Incorrect answers",
        "",
        *(
            f"- `{r.id}` ({r.type}) {r.question} — judge: {r.reason}"
            for r in records
            if r.correct is False
        ),
    ]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:16]))
    return summary


def _label(out: Path, labeller: str, limit: int | None) -> int:
    records = load_records(out / "records.jsonl")
    if not records:
        print("no records yet: run `mizan-eval run` first", file=sys.stderr)
        return 2
    cache: dict[str, str] = {}

    def gold(record: Record) -> str:
        if record.id not in cache:
            cache[record.id] = asyncio.run(
                _gold_texts(record.gold, date.fromisoformat(record.as_of))
            )
        return cache[record.id]

    added = label_session(
        records, out / "human_labels.jsonl", labeller=labeller, gold_text=gold, limit=limit
    )
    print(f"{added} labels added; total {len(load_labels(out / 'human_labels.jsonl'))}")
    return 0


def _kappa(out: Path) -> int:
    result = agreement(load_records(out / "records.jsonl"), load_labels(out / "human_labels.jsonl"))
    print(f"n={result.n} observed={result.observed:.3f} expected={result.expected:.3f}")
    print(f"Cohen's kappa = {result.kappa:.3f} ({result.interpretation}); target >= {KAPPA_TARGET}")
    print(f"confusion (judge -> human): {result.confusion}")
    return 0 if result.kappa >= KAPPA_TARGET else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mizan-eval")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    p_run.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p_run.add_argument("--sample", type=int, default=60, help="stratified sample size; 0 = all")
    p_run.add_argument("--pace", type=float, default=45.0, help="seconds between questions")
    p_run.add_argument("--graph", action="store_true", help="graph-aware search (F4)")
    p_run.add_argument("--decisions", action="store_true", help="court decisions in context")
    p_report = sub.add_parser("report")
    p_report.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p_label = sub.add_parser("label")
    p_label.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p_label.add_argument("--labeller", required=True)
    p_label.add_argument("--limit", type=int)
    p_kappa = sub.add_parser("kappa")
    p_kappa.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    configure_logging(settings.log.level, json_format=settings.log.json_format, stream=sys.stderr)

    if args.command == "run":
        return asyncio.run(
            _run(
                args.dataset,
                args.out,
                args.sample,
                args.pace,
                graph=args.graph,
                decisions=args.decisions,
            )
        )
    if args.command == "report":
        _report(args.out, list(load_records(args.out / "records.jsonl").values()))
        return 0
    if args.command == "label":
        return _label(args.out, args.labeller, args.limit)
    return _kappa(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
