"""Command line entry point.

mizan-train synth [--limit N]        synthetic questions per article (resumable, paced for Groq)
mizan-train build                    split, leakage guard, hard negatives -> examples.jsonl
mizan-train embed --name RUN [--layers 6 --lr 2e-5 --epochs 3]   fine-tune the embedding model
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path

from mizan_core.config import get_settings
from mizan_core.llm_clients import client_for, split_model
from mizan_core.logging import configure_logging
from mizan_graph.build import build_references
from mizan_graph.store import Direction, InMemoryGraph
from mizan_ingestion.chunking import Strategy
from mizan_retrieval import snapshot
from mizan_retrieval.cli import DEFAULT_SNAPSHOT, _embedders, _qdrant
from mizan_retrieval.corpus import ChunkRecord
from mizan_retrieval.search import Hit, Mode, SearchConfig, Searcher, merge_article
from mizan_train.dataset import (
    BuildReport,
    Example,
    build_examples,
    hard_negatives,
    read_examples,
    write_examples,
)
from mizan_train.embed import TrainConfig, train
from mizan_train.synth import load_pairs, synthesise

LABOUR_CODE = 46943
TODAY = date(2026, 10, 13)  # the corpus snapshot's "today", as in the eval datasets
DATA = Path("data/train")
DEFAULTS = TrainConfig()
EVAL_SETS = [
    Path("evals/datasets/v0/retrieval.jsonl"),
    Path("evals/datasets/v1/questions.jsonl"),
    Path("evals/datasets/v1/multihop.jsonl"),
    Path("evals/datasets/v1/multihop_b.jsonl"),
    Path("evals/datasets/v1/court_decisions.jsonl"),
]


def current_articles(records: list[ChunkRecord], on: date = TODAY) -> list[tuple[str, str]]:
    """Whole article texts in force on ``on`` (chunks merged), in a stable order."""
    by_article: dict[str, list[Hit]] = defaultdict(list)
    for r in records:
        if r.strategy is Strategy.ARTICLE and r.valid_from <= on < r.valid_to:
            by_article[r.article_key].append(
                Hit(r.key, r.article_key, r.parent_key, r.text, 0.0, r.valid_from, r.cites)
            )
    return [(key, merge_article(hits)) for key, hits in sorted(by_article.items())]


def protected_questions() -> list[str]:
    out = []
    for path in EVAL_SETS:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line)["question"])
    return out


async def _synth(limit: int | None, generator: str, judge: str, pace_s: float) -> int:
    settings = get_settings()
    gen_provider, gen_model = split_model(generator)
    judge_provider, judge_model = split_model(judge)
    articles = current_articles(snapshot.read(DEFAULT_SNAPSHOT))
    articles = [(k, t) for k, t in articles if k.startswith("art:")][:limit]
    gen_client = client_for(gen_provider, settings.llm)
    judge_client = client_for(judge_provider, settings.llm)
    try:
        pairs = await synthesise(
            (gen_client, judge_client),
            articles,
            DATA / "pairs.jsonl",
            generator=gen_model,
            judge=judge_model,
            pace_s=pace_s,
        )
    finally:
        await gen_client.close()
        await judge_client.close()
    kept = sum(p.kept for p in pairs)
    print(json.dumps({"articles": len(articles), "pairs": len(pairs), "kept": kept}))
    return 0


async def _build(negatives_k: int) -> int:
    records = snapshot.read(DEFAULT_SNAPSHOT)
    report = BuildReport()
    examples = build_examples(
        load_pairs(DATA / "pairs.jsonl"), protected_questions(), report=report
    )

    graph = InMemoryGraph(LABOUR_CODE, build_references(records))
    settings = get_settings()
    client = _qdrant(settings)
    config = SearchConfig(Mode.DENSE, dense_model="bge-m3", strategy=Strategy.ARTICLE)
    searcher = Searcher(client, _embedders(settings, ["bge-m3"]))
    mined: list[Example] = []
    try:
        for e in examples:
            hits = await searcher.search(
                e.question, source_id=LABOUR_CODE, as_of=TODAY, config=config, k=40
            )
            ranked = list(dict.fromkeys(h.article_key for h in hits))
            found = await graph.neighbours(
                LABOUR_CODE, [e.positive], as_of=TODAY, direction=Direction.BOTH
            )
            negatives = hard_negatives(
                ranked, e.positive, related={n.key for n in found}, k=negatives_k
            )
            mined.append(Example(e.question, e.positive, negatives, e.split))
    finally:
        await client.close()
    write_examples(DATA / "examples.jsonl", mined)
    print(json.dumps(asdict(report), ensure_ascii=False))
    return 0


def _embed(name: str, cfg: TrainConfig) -> int:
    articles = dict(current_articles(snapshot.read(DEFAULT_SNAPSHOT)))
    examples = read_examples(DATA / "examples.jsonl")
    report = train(examples, articles, cfg, DATA / "runs" / name)
    print(json.dumps({k: v for k, v in report.items() if k != "history"}, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mizan-train")
    sub = parser.add_subparsers(dest="command", required=True)
    p_synth = sub.add_parser("synth", help="synthetic questions per article")
    p_synth.add_argument("--limit", type=int)
    # provider:model; different model families for writing and checking the questions.
    p_synth.add_argument("--generator", default="gemini:gemini-2.5-flash")
    p_synth.add_argument("--judge", default="gemini:gemini-2.5-flash-lite")
    p_synth.add_argument("--pace", type=float, default=0.0)
    p_build = sub.add_parser("build", help="split, leakage guard, hard negatives")
    p_build.add_argument("--negatives", type=int, default=5)
    p_embed = sub.add_parser("embed", help="fine-tune the embedding model")
    p_embed.add_argument("--name", required=True)
    p_embed.add_argument("--layers", type=int, default=DEFAULTS.trainable_layers)
    p_embed.add_argument("--lr", type=float, default=DEFAULTS.lr)
    p_embed.add_argument("--epochs", type=int, default=DEFAULTS.epochs)
    p_embed.add_argument("--batch-size", type=int, default=DEFAULTS.batch_size)

    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    configure_logging(settings.log.level, json_format=settings.log.json_format, stream=sys.stderr)
    if args.command == "embed":
        cfg = TrainConfig(
            trainable_layers=args.layers, lr=args.lr, epochs=args.epochs, batch_size=args.batch_size
        )
        return _embed(args.name, cfg)
    if args.command == "synth":
        return asyncio.run(_synth(args.limit, args.generator, args.judge, args.pace))
    return asyncio.run(_build(args.negatives))


if __name__ == "__main__":
    raise SystemExit(main())
