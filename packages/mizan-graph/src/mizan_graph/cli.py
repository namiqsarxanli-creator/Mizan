"""Command line entry point.

mizan-graph build [--from-snapshot evals/snapshots/labour_code.jsonl.gz]
mizan-graph neighbours art:70 [--as-of 2020-01-01] [--direction both]
mizan-graph eval [--graph neo4j|memory] [--out evals/results/f4_graph]
mizan-graph gate [--update-baseline]      the CI eval gate for the serving path (dense + graph)
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from neo4j import AsyncDriver, AsyncGraphDatabase
from sqlalchemy import select

from mizan_core.config import Settings, get_settings
from mizan_core.logging import configure_logging
from mizan_graph.build import BuildStats, Reference, build_references
from mizan_graph.decisions import Decision, act_name, load_decisions
from mizan_graph.evaluate import (
    BASELINE,
    LABOUR_CODE,
    PRIMARY,
    SECONDARY,
    policy_grid,
    precompute,
    run_policy,
)
from mizan_graph.search import SERVING_POLICY, GraphSearcher
from mizan_graph.store import Direction, GraphStore, InMemoryGraph, Neo4jGraph
from mizan_ingestion.chunking import Strategy
from mizan_ingestion.db.schema import LegalDocument
from mizan_ingestion.db.session import create_engine, session_factory
from mizan_retrieval import snapshot
from mizan_retrieval.cli import (
    DEFAULT_BASELINE,
    DEFAULT_DATASET,
    DEFAULT_SNAPSHOT,
    _embedders,
    _qdrant,
    _today_in_baku,
    run_gate,
    run_variant,
    serving_variant,
)
from mizan_retrieval.corpus import ChunkRecord, load_chunk_records
from mizan_retrieval.evaluation import Question, RunResult, load_dataset, paired_bootstrap
from mizan_retrieval.report import write_report
from mizan_retrieval.search import Mode, SearchConfig, Searcher

NL = chr(10)
TITLE = "Azərbaycan Respublikasının Əmək Məcəlləsi"
SELF_NAMES = frozenset({"əmək məcəllə"})
# v1 (written without the graph: no-harm check), the multi-hop set the slot policy was found
# on, and the pre-registered test set (see evals/results/f4_graph/PREREGISTRATION.md).
DEFAULT_DATASETS = [
    Path("evals/datasets/v1/questions.jsonl"),
    Path("evals/datasets/v1/multihop.jsonl"),
    Path("evals/datasets/v1/multihop_b.jsonl"),
]


def _driver(settings: Settings) -> AsyncDriver:
    return AsyncGraphDatabase.driver(
        settings.neo4j.uri,
        auth=(settings.neo4j.user, settings.neo4j.password.get_secret_value()),
    )


async def _records(from_snapshot: Path | None) -> list[ChunkRecord]:
    if from_snapshot is not None:
        frozen = await asyncio.to_thread(snapshot.read, from_snapshot)
        return [r for r in frozen if r.strategy is Strategy.ARTICLE]
    engine = create_engine(get_settings().postgres)
    try:
        return await load_chunk_records(session_factory(engine), LABOUR_CODE, Strategy.ARTICLE)
    finally:
        await engine.dispose()


async def _references(from_snapshot: Path | None) -> tuple[list[Reference], BuildStats]:
    stats = BuildStats()
    references = build_references(await _records(from_snapshot), stats=stats)
    return references, stats


async def _registry() -> tuple[list[tuple[int, str, str | None]], list[Decision]]:
    """Acts registered by `mizan-ingest linked`, and the decisions interpreting the Code."""
    engine = create_engine(get_settings().postgres)
    try:
        sessions = session_factory(engine)
        async with sessions() as session:
            rows = await session.execute(
                select(LegalDocument.source_id, LegalDocument.title, LegalDocument.doc_type)
            )
            acts = [(i, t, k) for i, t, k in rows.tuples()]
        decisions = await load_decisions(sessions, SELF_NAMES)
    finally:
        await engine.dispose()
    return acts, decisions


async def _build(from_snapshot: Path | None) -> int:
    references, stats = await _references(from_snapshot)
    acts, decisions = await _registry()
    registry = {
        name: i for i, title, kind in acts if (name := act_name(title, kind)) and i != LABOUR_CODE
    }
    driver = _driver(get_settings())
    try:
        graph = Neo4jGraph(driver)
        await graph.ensure_schema()
        await graph.replace_acts(acts)
        await graph.replace_references(LABOUR_CODE, TITLE, references, registry=registry)
        await graph.replace_decisions(LABOUR_CODE, decisions)
        counts = await graph.counts()
    finally:
        await driver.close()
    by_act = Counter(r.act for r in references)
    print(
        json.dumps(
            {
                "citations": stats.citations,
                "self_citations_skipped": stats.self_loops,
                "unresolved": stats.unresolved,
                "edges": len(references),
                "exception_edges": sum(r.exception for r in references),
                "edges_by_act": {
                    act: {"edges": n, "resolved_to": registry.get(act)}
                    for act, n in by_act.most_common()
                },
                "decisions": {d.eqanun_id: d.interprets for d in decisions},
                "neo4j": counts,
            },
            ensure_ascii=False,
            indent=1,
            default=str,
        )
    )
    return 0


async def _neighbours(key: str, as_of: date, direction: Direction) -> int:
    driver = _driver(get_settings())
    try:
        found = await Neo4jGraph(driver).neighbours(
            LABOUR_CODE, [key], as_of=as_of, direction=direction
        )
    finally:
        await driver.close()
    for n in sorted(found, key=lambda n: (n.direction, n.key)):
        arrow = "->" if n.direction is Direction.OUT else "<-"
        print(f"{n.seed} {arrow} {n.key}")
    return 0


def _paired(base: RunResult, candidate: RunResult) -> list[dict[str, object]]:
    return [
        {
            "metric": c.metric,
            "dense": round(c.baseline, 3),
            "graph": round(c.candidate, 3),
            "delta": str(c.delta),
            "verdict": "better"
            if c.significantly_better
            else ("worse" if c.significantly_worse else "no significant change"),
        }
        for c in paired_bootstrap(base.per_question, candidate.per_question)
    ]


async def _eval(datasets: list[Path], out: Path, backend: str, from_snapshot: Path) -> int:
    settings = get_settings()
    config = SearchConfig(Mode.DENSE, dense_model="bge-m3", strategy=Strategy.ARTICLE)
    driver: AsyncDriver | None = None
    graph: GraphStore
    if backend == "neo4j":
        driver = _driver(settings)
        graph = Neo4jGraph(driver)
    else:
        references, _ = await _references(from_snapshot)
        graph = InMemoryGraph(LABOUR_CODE, references)

    client = _qdrant(settings)
    base = Searcher(client, _embedders(settings, ["bge-m3"]))
    grid = policy_grid()
    summary: dict[str, object] = {
        "graph_backend": backend,
        "primary_policy": PRIMARY.label,
        "secondary_policy": SECONDARY.label,
    }
    grid_rows: dict[str, dict[str, float]] = {p.label: {} for p in grid}
    try:
        for path in datasets:
            questions = load_dataset(path)
            ranked = await precompute(base, questions, config)
            runs = {p: await run_policy(p, ranked, graph, config) for p in grid}
            name = path.stem
            compared = (("dense", BASELINE), ("primary", PRIMARY), ("secondary", SECONDARY))
            write_report(out / name, path, questions, [runs[p] for _, p in compared])
            by_type: dict[str, list[str]] = {}
            for q in questions:
                if q.id in runs[BASELINE].per_question:
                    by_type.setdefault(q.type, []).append(q.id)
            summary[name] = {
                "dataset": path.as_posix(),
                "questions": len(runs[BASELINE].per_question),
                "recall_at_5_by_type": {
                    t: {
                        label: round(runs[p].summary(ids)["Recall@5"].mean, 3)
                        for label, p in compared
                    }
                    for t, ids in sorted(by_type.items())
                },
                "primary_vs_dense": _paired(runs[BASELINE], runs[PRIMARY]),
                "secondary_vs_dense": _paired(runs[BASELINE], runs[SECONDARY]),
            }
            for p, run in runs.items():
                s = run.summary()
                grid_rows[p.label][f"{name} R@5"] = round(s["Recall@5"].mean, 3)
                grid_rows[p.label][f"{name} MRR"] = round(s["MRR@10"].mean, 3)
    finally:
        await client.close()
        if driver is not None:
            await driver.close()

    summary["grid"] = grid_rows
    text = json.dumps(summary, ensure_ascii=False, indent=1)
    await asyncio.to_thread(_write, out / "summary.json", text + NL)
    print(text)
    return 0


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _gate(dataset: Path, baseline: Path, update: bool, from_snapshot: Path) -> int:
    """Gate the serving path. The graph is rebuilt from the snapshot: CI needs no Neo4j."""
    config = serving_variant()

    async def run(questions: list[Question]) -> RunResult:
        references, _ = await _references(from_snapshot)
        graph = InMemoryGraph(LABOUR_CODE, references)
        settings = get_settings()
        client = _qdrant(settings)
        try:
            base = Searcher(client, _embedders(settings, [config.dense_model]))
            return await run_variant(GraphSearcher(base, graph, SERVING_POLICY), config, questions)
        finally:
            await client.close()

    label = f"{config.label} + {SERVING_POLICY.label}"
    return run_gate(dataset, baseline, update, label=label, run=run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mizan-graph")
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="extract citations and (re)write the Neo4j graph")
    p_build.add_argument("--from-snapshot", type=Path, help="build from a corpus snapshot")

    p_nb = sub.add_parser("neighbours", help="articles linked to an article by citations")
    p_nb.add_argument("key")
    p_nb.add_argument("--as-of", type=date.fromisoformat, default=_today_in_baku())
    p_nb.add_argument("--direction", type=Direction, default=Direction.BOTH)

    p_eval = sub.add_parser("eval", help="graph-aware vs dense retrieval")
    p_eval.add_argument("--datasets", type=Path, nargs="+", default=DEFAULT_DATASETS)
    p_eval.add_argument("--out", type=Path, default=Path("evals/results/f4_graph"))
    p_eval.add_argument("--graph", choices=["neo4j", "memory"], default="neo4j")
    p_eval.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)

    p_gate = sub.add_parser("gate", help="fail on a significant regression of the serving path")
    p_gate.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    p_gate.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    p_gate.add_argument("--update-baseline", action="store_true")
    p_gate.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)

    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    configure_logging(settings.log.level, json_format=settings.log.json_format, stream=sys.stderr)

    if args.command == "build":
        return asyncio.run(_build(args.from_snapshot))
    if args.command == "gate":
        return _gate(args.dataset, args.baseline, args.update_baseline, args.snapshot)
    if args.command == "neighbours":
        return asyncio.run(_neighbours(args.key, args.as_of, args.direction))
    return asyncio.run(_eval(args.datasets, args.out, args.graph, args.snapshot))


if __name__ == "__main__":
    raise SystemExit(main())
