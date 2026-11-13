"""Command line entry point.

mizan-retrieval index [--strategy article part] [--models bge-m3 e5-large]
mizan-retrieval search "sual" [--as-of 2020-01-01] [--mode hybrid] [--rerank 20]
mizan-retrieval eval [--dataset evals/datasets/v0/retrieval.jsonl] [--out evals/results]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import re
import sys
import time
from collections.abc import Callable, Coroutine
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import groq
from qdrant_client import AsyncQdrantClient

from mizan_core.config import Settings, get_settings
from mizan_core.logging import configure_logging, get_logger
from mizan_ingestion.chunking import Strategy
from mizan_ingestion.db.session import create_engine, session_factory
from mizan_retrieval import snapshot
from mizan_retrieval.corpus import load_chunk_records
from mizan_retrieval.embeddings import MODELS, Embedder, EmbeddingCache
from mizan_retrieval.evaluation import Question, RunResult, load_dataset, paired_bootstrap
from mizan_retrieval.index import build_index
from mizan_retrieval.report import write_report
from mizan_retrieval.rerank import Reranker
from mizan_retrieval.rewrite import GroqQueryRewriter
from mizan_retrieval.search import Mode, SearchBackend, SearchConfig, Searcher

log = get_logger(__name__)
LABOUR_CODE = 46943
_RECALL_FLOOR = 0.75  # F2 Definition of Done


def _today_in_baku() -> date:
    # "The law today" means today in Azerbaijan, whatever the server's timezone.
    return datetime.now(ZoneInfo("Asia/Baku")).date()


def _qdrant(settings: Settings) -> AsyncQdrantClient:
    key = settings.qdrant.api_key.get_secret_value() if settings.qdrant.api_key else None
    return AsyncQdrantClient(url=settings.qdrant.url, api_key=key, timeout=120)


def _embedders(settings: Settings, names: list[str]) -> dict[str, Embedder]:
    cache = EmbeddingCache(Path(settings.ingestion.raw_dir).parent / "embeddings" / "cache.sqlite")
    return {n: Embedder(MODELS[n], cache) for n in names}


async def _index(
    strategies: list[Strategy], model_names: list[str], from_snapshot: Path | None = None
) -> int:
    settings = get_settings()
    # A snapshot needs neither Postgres nor e-qanun.az: this is how CI builds its index.
    engine = None if from_snapshot else create_engine(settings.postgres)
    client = _qdrant(settings)
    embedders = list(_embedders(settings, model_names).values())
    try:
        for strategy in strategies:
            if from_snapshot is not None:
                frozen = await asyncio.to_thread(snapshot.read, from_snapshot)
                records = [r for r in frozen if r.strategy is strategy]
            elif engine is not None:
                records = await load_chunk_records(session_factory(engine), LABOUR_CODE, strategy)
            else:  # pragma: no cover - engine exists whenever there is no snapshot
                raise RuntimeError("no corpus source")
            started = time.perf_counter()
            name = await build_index(client, strategy, records, embedders)
            print(
                json.dumps(
                    {
                        "strategy": strategy.value,
                        "collection": name,
                        "points": len(records),
                        "seconds": round(time.perf_counter() - started, 1),
                    }
                )
            )
    finally:
        await client.close()
        if engine is not None:
            await engine.dispose()
    return 0


async def _snapshot(out: Path, strategies: list[Strategy]) -> int:
    engine = create_engine(get_settings().postgres)
    try:
        records = []
        for strategy in strategies:
            records += await load_chunk_records(session_factory(engine), LABOUR_CODE, strategy)
    finally:
        await engine.dispose()
    digest = await asyncio.to_thread(snapshot.write, out, records)
    print(json.dumps({"snapshot": out.as_posix(), "records": len(records), "hash": digest}))
    return 0


# Serving configuration (ADR-0004): what the gate protects.
def serving_variant() -> SearchConfig:
    r = get_settings().retrieval
    return SearchConfig(
        mode=Mode(r.mode),
        dense_model=r.dense_model,
        sparse=r.sparse,
        strategy=Strategy(r.strategy),
        candidates=r.candidates,
        rerank_top=r.rerank_top,
        refs=r.article_refs,
    )


async def _run_serving(questions: list[Question], config: SearchConfig) -> RunResult:
    settings = get_settings()
    client = _qdrant(settings)
    try:
        searcher = Searcher(client, _embedders(settings, [config.dense_model]), None)
        return await run_variant(searcher, config, questions)
    finally:
        await client.close()


def _gate(dataset: Path, baseline_path: Path, update: bool) -> int:
    config = serving_variant()
    return run_gate(
        dataset,
        baseline_path,
        update,
        label=config.label,
        run=lambda questions: _run_serving(questions, config),
    )


def run_gate(
    dataset: Path,
    baseline_path: Path,
    update: bool,
    *,
    label: str,
    run: Callable[[list[Question]], Coroutine[Any, Any, RunResult]],
) -> int:
    """Fail (exit 1) when the serving configuration is significantly worse than the baseline.

    ``run`` evaluates the serving configuration; packages that extend retrieval (the knowledge
    graph) gate their own serving path through it.
    """
    questions = load_dataset(dataset)
    result = asyncio.run(run(questions))

    dataset_hash = hashlib.sha256(dataset.read_bytes()).hexdigest()[:16]
    if update:
        payload = {
            "dataset": dataset.as_posix(),
            "dataset_hash": dataset_hash,
            "variant": label,
            "per_question": result.per_question,
        }
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        print(f"baseline written: {baseline_path} ({len(result.per_question)} questions)")
        return 0

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if baseline["dataset_hash"] != dataset_hash:
        print("dataset changed since the baseline; run `mizan-retrieval gate --update-baseline`")
        return 2
    comparisons = paired_bootstrap(baseline["per_question"], result.per_question)
    floor = result.summary()["Recall@5"]
    lines = [
        f"### Retrieval eval gate: `{label}` on `{dataset.as_posix()}`",
        "",
        "| Metric | Baseline | This change | Delta [95% CI] | |",
        "|---|---|---|---|---|",
    ]
    failed = False
    for c in comparisons:
        verdict = (
            "regression"
            if c.significantly_worse
            else ("improvement" if c.significantly_better else "no significant change")
        )
        failed |= c.significantly_worse
        lines.append(
            f"| {c.metric} | {c.baseline:.3f} | {c.candidate:.3f} | "
            f"{c.delta.mean:+.3f} [{c.delta.low:+.3f}, {c.delta.high:+.3f}] | {verdict} |"
        )
    if floor.mean < _RECALL_FLOOR:
        failed = True
        lines.append(f"\nRecall@5 {floor.mean:.3f} is below the floor of {_RECALL_FLOOR}.")
    lines.append("")
    lines.append("**FAILED**: significant regression." if failed else "**PASSED**")
    report = "\n".join(lines)
    print(report)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    return 1 if failed else 0


async def _search(query: str, as_of: date, config: SearchConfig, k: int) -> int:
    settings = get_settings()
    client = _qdrant(settings)
    searcher = Searcher(
        client,
        _embedders(settings, [config.dense_model]),
        Reranker() if config.rerank_top else None,
    )
    try:
        hits = await searcher.search(query, source_id=LABOUR_CODE, as_of=as_of, config=config, k=k)
    finally:
        await client.close()
    print(f"# {config.label}, as of {as_of}")
    for i, hit in enumerate(hits, 1):
        first_line = hit.text.splitlines()[0].rsplit(" > ", 1)[-1]
        print(f"{i:>2}. {hit.key:<24} {hit.score:8.4f}  {first_line[:90]}")
    return 0


def default_variants() -> list[SearchConfig]:
    art, part = Strategy.ARTICLE, Strategy.PART
    return [
        # Lexical: does Azerbaijani prefix stemming help, and at what length?
        SearchConfig(Mode.SPARSE, sparse="bm25", strategy=art),
        SearchConfig(Mode.SPARSE, sparse="bm25_p5", strategy=art),
        SearchConfig(Mode.SPARSE, sparse="bm25_p6", strategy=art),
        SearchConfig(Mode.SPARSE, sparse="bm25_p7", strategy=art),
        # Dense: which multilingual model handles colloquial Azerbaijani better?
        SearchConfig(Mode.DENSE, dense_model="bge-m3", strategy=art),
        SearchConfig(Mode.DENSE, dense_model="e5-large", strategy=art),
        # Chunk granularity.
        SearchConfig(Mode.DENSE, dense_model="bge-m3", strategy=part),
        SearchConfig(Mode.SPARSE, sparse="bm25_p5", strategy=part),
        # Hybrid and reranking.
        SearchConfig(Mode.HYBRID, dense_model="bge-m3", sparse="bm25_p5", strategy=art),
        SearchConfig(Mode.HYBRID, dense_model="e5-large", sparse="bm25_p5", strategy=art),
        SearchConfig(Mode.HYBRID, dense_model="bge-m3", sparse="bm25_p5", strategy=part),
        SearchConfig(
            Mode.HYBRID, dense_model="bge-m3", sparse="bm25_p5", strategy=art, rerank_top=20
        ),
        SearchConfig(
            Mode.HYBRID, dense_model="bge-m3", sparse="bm25_p5", strategy=part, rerank_top=20
        ),
        # Query rewriting (LLM restates the question in the Code's terminology).
        SearchConfig(Mode.SPARSE, sparse="bm25_p5", strategy=art, rewrite=True),
        SearchConfig(
            Mode.HYBRID, dense_model="bge-m3", sparse="bm25_p5", strategy=art, rewrite=True
        ),
        # Added after the first run showed hybrid fusion hurting MRR: rerank dense alone.
        SearchConfig(Mode.DENSE, dense_model="bge-m3", strategy=art, rerank_top=20),
        SearchConfig(Mode.DENSE, dense_model="bge-m3", strategy=art, rewrite=True),
    ]


async def run_variant(
    searcher: SearchBackend,
    config: SearchConfig,
    questions: list[Question],
    rewrites: dict[str, str] | None = None,
) -> RunResult:
    result = RunResult(config.label)
    for q in questions:
        if not q.relevant:  # out-of-scope questions have no recall to measure
            continue
        started = time.perf_counter()
        # Part chunks: look deeper so that 10 distinct *articles* can be ranked.
        k = 30 if config.strategy is Strategy.PART else 10
        query = (rewrites or {}).get(q.id, q.question) if config.rewrite else q.question
        hits = await searcher.search(
            query, source_id=LABOUR_CODE, as_of=q.as_of, config=config, k=k
        )
        result.add(q, [h.article_key for h in hits], (time.perf_counter() - started) * 1000)
    return result


def _read_json(path: Path) -> dict[str, str]:
    return dict(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else {}


def _write_json(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


async def load_rewrites(questions: list[Question], path: Path) -> dict[str, str]:
    """Expanded queries, computed once and stored so reruns are free and reproducible."""
    cached = await asyncio.to_thread(_read_json, path)
    todo = [q for q in questions if q.id not in cached]
    if not todo:
        return cached
    settings = get_settings()
    if settings.llm.groq_api_key is None:
        raise SystemExit("rewrite variants need MIZAN_LLM__GROQ_API_KEY")
    client = groq.AsyncGroq(api_key=settings.llm.groq_api_key.get_secret_value())
    # Generous timeout: this runs once, offline, and a timeout silently degrades the variant.
    rewriter = GroqQueryRewriter(client, model=settings.llm.rewrite_model, timeout_s=30)
    try:
        for q in todo:
            expanded = await rewriter.expand(q.question)
            if expanded != q.question:  # failures are retried next run, never cached
                cached[q.id] = expanded
    finally:
        await client.close()
    await asyncio.to_thread(_write_json, path, cached)
    return cached


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


def _load_checkpoint(path: Path, label: str) -> RunResult | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return RunResult(label, per_question=data["per_question"], latency_ms=data["latency_ms"])


def _save_checkpoint(path: Path, result: RunResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"per_question": result.per_question, "latency_ms": result.latency_ms}
    path.write_text(json.dumps(payload), encoding="utf-8")


async def _eval(dataset: Path, out_dir: Path, only: list[int] | None) -> int:
    settings = get_settings()
    questions = load_dataset(dataset)
    variants = default_variants()
    if only:
        variants = [variants[i] for i in only]
    rewrites = (
        await load_rewrites(questions, out_dir / "rewrites.json")
        if any(v.rewrite for v in variants)
        else None
    )
    client = _qdrant(settings)
    searcher = Searcher(client, _embedders(settings, list(MODELS)), Reranker())
    results = []
    try:
        for config in variants:
            # One file per variant, written as soon as it finishes: an interrupted eval resumes
            # instead of recomputing hours of CPU reranking.
            checkpoint = out_dir / "variants" / f"{_slug(config.label)}.json"
            result = await asyncio.to_thread(_load_checkpoint, checkpoint, config.label)
            if result is None:
                log.info("eval_variant", variant=config.label)
                result = await run_variant(searcher, config, questions, rewrites)
                await asyncio.to_thread(_save_checkpoint, checkpoint, result)
            results.append(result)
            summary = result.summary()
            print(f"{config.label:<55} Hit@5 {summary['Hit@5']}  MRR {summary['MRR@10']}")
    finally:
        await client.close()
    write_report(out_dir, dataset, questions, results)
    print(f"report: {out_dir}")
    return 0


DEFAULT_DATASET = Path("evals/datasets/v0/retrieval.jsonl")
DEFAULT_SNAPSHOT = Path("evals/snapshots/labour_code.jsonl.gz")
DEFAULT_BASELINE = Path("evals/baselines/retrieval.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mizan-retrieval")
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="(re)build Qdrant collections")
    p_index.add_argument(
        "--strategy", nargs="+", type=Strategy, default=[Strategy.ARTICLE, Strategy.PART]
    )
    p_index.add_argument("--models", nargs="+", choices=list(MODELS), default=list(MODELS))
    p_index.add_argument("--from-snapshot", type=Path, help="build from a corpus snapshot")

    p_snap = sub.add_parser("snapshot", help="freeze the chunk corpus for reproducible evals")
    p_snap.add_argument("--out", type=Path, default=DEFAULT_SNAPSHOT)
    p_snap.add_argument("--strategy", nargs="+", type=Strategy, default=[Strategy.ARTICLE])

    p_gate = sub.add_parser("gate", help="fail on a significant regression vs the baseline")
    p_gate.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    p_gate.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    p_gate.add_argument("--update-baseline", action="store_true")

    p_search = sub.add_parser("search", help="search the Labour Code")
    p_search.add_argument("query")
    p_search.add_argument("--as-of", type=date.fromisoformat, default=_today_in_baku())
    p_search.add_argument("--mode", type=Mode, default=Mode.HYBRID)
    p_search.add_argument("--model", choices=list(MODELS), default="bge-m3")
    p_search.add_argument("--sparse", default="bm25_p5")
    p_search.add_argument("--strategy", type=Strategy, default=Strategy.ARTICLE)
    p_search.add_argument("--rerank", type=int, default=0, metavar="TOP")
    p_search.add_argument("-k", type=int, default=10)

    p_eval = sub.add_parser("eval", help="compare retrieval variants on a dataset")
    p_eval.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    p_eval.add_argument("--out", type=Path, default=Path("evals/results/f2_retrieval"))
    p_eval.add_argument("--only", type=int, nargs="+", help="indices of variants to run")

    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    configure_logging(settings.log.level, json_format=settings.log.json_format, stream=sys.stderr)

    if args.command == "index":
        return asyncio.run(_index(args.strategy, args.models, args.from_snapshot))
    if args.command == "snapshot":
        return asyncio.run(_snapshot(args.out, args.strategy))
    if args.command == "gate":
        return _gate(args.dataset, args.baseline, args.update_baseline)
    if args.command == "search":
        config = SearchConfig(
            mode=args.mode,
            dense_model=args.model,
            sparse=args.sparse,
            strategy=args.strategy,
            rerank_top=args.rerank,
        )
        return asyncio.run(_search(args.query, args.as_of, config, args.k))
    return asyncio.run(_eval(args.dataset, args.out, args.only))


if __name__ == "__main__":
    raise SystemExit(main())
