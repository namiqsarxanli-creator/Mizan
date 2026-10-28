"""Command line entry point.

mizan-train synth [--limit N]        synthetic questions per article (resumable, paced for Groq)
mizan-train build                    split, leakage guard, hard negatives -> examples.jsonl
mizan-train embed --name RUN [--layers 6 --lr 2e-5 --epochs 3]   fine-tune the embedding model
mizan-train compare [--candidate bge-m3-mizan]   base vs fine-tuned retrieval on the eval sets
mizan-train candidates [--k 20]      dense top-k per question, for reranker distillation
mizan-train rerank-report            dense vs teacher vs student (+ONNX int8): quality, latency
mizan-train tokenizers               token efficiency of candidate base models on Azerbaijani
mizan-train verify-synth [--batch 8] constructed verification claims (F8 verifier data)
mizan-train verify-api [--split test] the production (API) verifier on the constructed claims
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from mizan_core.config import get_settings
from mizan_core.llm_clients import OpenAICompatible, client_for, split_model
from mizan_core.logging import configure_logging, get_logger
from mizan_graph.build import build_references
from mizan_graph.store import Direction, InMemoryGraph
from mizan_ingestion.chunking import Strategy
from mizan_retrieval import snapshot
from mizan_retrieval.cli import DEFAULT_SNAPSHOT, _embedders, _qdrant, run_variant
from mizan_retrieval.corpus import ChunkRecord
from mizan_retrieval.evaluation import bootstrap, load_dataset, paired_bootstrap
from mizan_retrieval.search import Hit, Mode, SearchConfig, Searcher, merge_article
from mizan_train.dataset import (
    BuildReport,
    Example,
    build_examples,
    hard_negatives,
    read_examples,
    write_examples,
)
from mizan_train.distill import (
    STUDENT,
    TEACHER,
    Candidates,
    per_question,
    rank_metrics,
    read_candidates,
    retriever_order,
    score,
    write_candidates,
)
from mizan_train.embed import TrainConfig, train
from mizan_train.synth import load_pairs, synthesise
from mizan_train.verify_data import synthesise_claims

log = get_logger(__name__)

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


def article_versions(records: list[ChunkRecord]) -> dict[str, str]:
    """Every version of every article, keyed "article|valid_from" (chunks merged)."""
    by_version: dict[str, list[Hit]] = defaultdict(list)
    for r in records:
        if r.strategy is Strategy.ARTICLE:
            by_version[f"{r.article_key}|{r.valid_from.isoformat()}"].append(
                Hit(r.key, r.article_key, r.parent_key, r.text, 0.0, r.valid_from, r.cites)
            )
    return {key: merge_article(hits) for key, hits in sorted(by_version.items())}


def protected_questions() -> list[str]:
    out = []
    for path in EVAL_SETS:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line)["question"])
    return out


async def _synth(
    limit: int | None, generators: list[str], judge: str, pace_s: float, batch: int
) -> int:
    settings = get_settings()
    articles = current_articles(snapshot.read(DEFAULT_SNAPSHOT))
    articles = [(k, t) for k, t in articles if k.startswith("art:")][:limit]
    clients: dict[str, OpenAICompatible] = {}

    def resolve(spec: str) -> tuple[OpenAICompatible, str]:
        provider, model = split_model(spec)
        if provider not in clients:
            clients[provider] = client_for(provider, settings.llm)
        return clients[provider], model

    try:
        pairs = await synthesise(
            [resolve(g) for g in generators],
            resolve(judge),
            articles,
            DATA / "pairs.jsonl",
            batch=batch,
            pace_s=pace_s,
        )
    finally:
        for client in clients.values():
            await client.close()
    kept = sum(p.kept for p in pairs)
    done = len({p.article for p in pairs})
    print(json.dumps({"articles": len(articles), "done": done, "pairs": len(pairs), "kept": kept}))
    return 0


async def _verify_synth(
    limit: int | None, generators: list[str], judge: str, pace_s: float, batch: int
) -> int:
    settings = get_settings()
    articles = current_articles(snapshot.read(DEFAULT_SNAPSHOT))
    articles = [(k, t) for k, t in articles if k.startswith("art:")][:limit]
    clients: dict[str, OpenAICompatible] = {}

    def resolve(spec: str) -> tuple[OpenAICompatible, str]:
        provider, model = split_model(spec)
        if provider not in clients:
            clients[provider] = client_for(provider, settings.llm)
        return clients[provider], model

    try:
        claims = await synthesise_claims(
            [resolve(g) for g in generators],
            resolve(judge),
            articles,
            DATA / "verify_claims.jsonl",
            batch=batch,
            pace_s=pace_s,
        )
    finally:
        for client in clients.values():
            await client.close()
    by_kind: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for c in claims:
        by_kind[c.kind][0] += c.kept
        by_kind[c.kind][1] += 1
    print(
        json.dumps(
            {
                "articles": len(articles),
                "done": len({c.article for c in claims}),
                "claims": len(claims),
                "kept": sum(c.kept for c in claims),
                "kept_by_kind": {k: f"{a}/{n}" for k, (a, n) in sorted(by_kind.items())},
            }
        )
    )
    return 0


class _UsageClient:
    """Wraps the Groq client and keeps the last response's token usage."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.chat = self
        self.completions = self
        self.last: Any = None

    async def create(self, **request: Any) -> Any:
        response = await self._client.chat.completions.create(**request)
        self.last = getattr(response, "usage", None)
        return response


async def _verify_api(split: str, pace_s: float) -> int:
    """Score claims with ``mizan_agents.agents.verify`` exactly as production calls it."""
    import groq

    from mizan_agents.agents import verify
    from mizan_agents.gateway import DEFAULT_ROUTES, GatewayError, LLMGateway, Role, TokenBudget
    from mizan_agents.schemas import Claim, Evidence
    from mizan_train.verify_data import load_claims

    settings = get_settings()
    if settings.llm.groq_api_key is None:
        raise SystemExit("verify-api needs MIZAN_LLM__GROQ_API_KEY")
    texts = dict(current_articles(snapshot.read(DEFAULT_SNAPSHOT)))
    claims = [c for c in load_claims(DATA / "verify_claims.jsonl") if c.kept and c.split == split]
    out = DATA / f"verify_api_{split}.jsonl"
    done = (
        {json.loads(line)["key"] for line in out.read_text("utf-8").splitlines() if line}
        if out.exists()
        else set()
    )
    client = _UsageClient(groq.AsyncGroq(api_key=settings.llm.groq_api_key.get_secret_value()))
    gateway = LLMGateway(client)
    model = DEFAULT_ROUTES[Role.VERIFIER].model
    try:
        for c in claims:
            key = f"{c.article}|{c.kind}"
            if key in done:
                continue
            evidence = Evidence(
                key=c.article, title="", text=texts[c.article], in_force_since=TODAY
            )
            started = time.perf_counter()
            try:
                checks = await verify(
                    gateway,
                    [Claim(text=c.claim, source=c.article, quote=c.claim)],
                    [evidence],
                    budget=TokenBudget(100_000),
                )
            except GatewayError as exc:
                log.warning("verify_api_failed", key=key, error=str(exc)[:160])
                continue  # not recorded: the next run retries it
            usage = client.last
            row = {
                "key": key,
                "kind": c.kind,
                "label": c.label,
                "pred": checks[0].support.value,
                "model": model,
                "input_tokens": getattr(usage, "prompt_tokens", 0),
                "output_tokens": getattr(usage, "completion_tokens", 0),
                "latency_s": round(time.perf_counter() - started, 3),
            }
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            done.add(key)
            if pace_s:
                await asyncio.sleep(pace_s)
    finally:
        await client._client.close()
    print(json.dumps({"split": split, "claims": len(claims), "scored": len(done)}))
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


async def _candidates(k: int) -> int:
    """Dense top-k articles per question: synthetic splits and every eval set."""
    questions: list[tuple[str, str, str, list[str], date]] = [
        (f"syn{i}", e.question, e.split, [e.positive], TODAY)
        for i, e in enumerate(read_examples(DATA / "examples.jsonl"))
    ]
    for path in EVAL_SETS:
        split = f"eval:{path.stem}"
        questions += [
            (q.id, q.question, split, sorted(q.relevant), q.as_of)
            for q in load_dataset(path)
            if q.relevant
        ]
    settings = get_settings()
    client = _qdrant(settings)
    searcher = Searcher(client, _embedders(settings, ["bge-m3"]))
    config = SearchConfig(Mode.DENSE, dense_model="bge-m3", strategy=Strategy.ARTICLE)
    rows = []
    try:
        for qid, text, split, gold, as_of in questions:
            hits = await searcher.search(
                text, source_id=LABOUR_CODE, as_of=as_of, config=config, k=2 * k
            )
            seen: dict[str, str] = {}
            for h in hits:
                seen.setdefault(h.article_key, f"{h.article_key}|{h.valid_from.isoformat()}")
            top = list(seen.items())[:k]
            rows.append(
                Candidates(qid, text, split, gold, [a for a, _ in top], [p for _, p in top])
            )
    finally:
        await client.close()
    write_candidates(DATA / "rerank_candidates.jsonl", rows)
    texts = article_versions(snapshot.read(DEFAULT_SNAPSHOT))
    (DATA / "article_versions.json").write_text(json.dumps(texts, ensure_ascii=False), "utf-8")
    recall = sum(any(g in r.articles for g in r.gold) for r in rows) / len(rows)
    print(json.dumps({"questions": len(rows), "texts": len(texts), f"gold_in_top{k}": recall}))
    return 0


# The F7 gate, fixed before the comparison ran: dense article retrieval on the main v1 set,
# Recall@5 up by at least 8% relative to the base model.
PRIMARY_SET = Path("evals/datasets/v1/questions.jsonl")
MIN_GAIN = 0.08


async def _compare(base: str, candidate: str) -> dict[str, Any]:
    settings = get_settings()
    client = _qdrant(settings)
    searcher = Searcher(client, _embedders(settings, [base, candidate]))
    report: dict[str, Any] = {"base": base, "candidate": candidate}
    try:
        for path in EVAL_SETS:
            questions = load_dataset(path)
            runs = {}
            for model in (base, candidate):
                config = SearchConfig(Mode.DENSE, dense_model=model, strategy=Strategy.ARTICLE)
                runs[model] = (await run_variant(searcher, config, questions)).per_question
            rows = paired_bootstrap(runs[base], runs[candidate], metrics=("Recall@5", "MRR@10"))
            report[str(path)] = {"n": len(runs[base])} | {
                c.metric: {
                    "base": round(c.baseline, 3),
                    "candidate": round(c.candidate, 3),
                    "delta": [round(x, 3) for x in (c.delta.mean, c.delta.low, c.delta.high)],
                    "significant": c.significantly_better or c.significantly_worse,
                }
                for c in rows
            }
    finally:
        await client.close()
    primary = report[str(PRIMARY_SET)]["Recall@5"]
    gain = (primary["candidate"] - primary["base"]) / primary["base"]
    report["gate"] = {"relative_recall5_gain": round(gain, 3), "passed": gain >= MIN_GAIN}
    return report


RERANK = DATA / "rerank"
MIN_RETENTION = 0.95  # pre-registered: evals/results/f7_rerank/PREREGISTRATION.md


def _latency(model: Any, rows: list[Candidates], texts: dict[str, str]) -> dict[str, float]:
    """p50 / p95 seconds to rescore one question's candidate list on this CPU."""
    seconds = []
    for r in rows:
        started = time.perf_counter()
        score(model, [r], texts, batch_size=16)
        seconds.append(time.perf_counter() - started)
    ordered = sorted(seconds)
    return {
        "p50_s": round(statistics.median(ordered), 3),
        "p95_s": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 3),
    }


def _rerank_report(latency_questions: int) -> dict[str, Any]:
    from sentence_transformers import CrossEncoder

    from mizan_train.onnx_export import OnnxCrossEncoder, export

    rows = read_candidates(RERANK / "scored_candidates.jsonl")
    student_scores = {
        r["split"] + "/" + r["id"]: r["student"]
        for r in map(json.loads, (RERANK / "all_scores.jsonl").read_text("utf-8").splitlines())
    }
    texts = json.loads((DATA / "article_versions.json").read_text(encoding="utf-8"))
    evals = [r for r in rows if r.split.startswith("eval:")]
    model_dir = RERANK / "student"
    onnx_dir = RERANK / "onnx"
    if not (onnx_dir / "model.int8.onnx").exists():
        export(model_dir, onnx_dir)
    int8 = OnnxCrossEncoder(onnx_dir)
    systems = {
        "dense": retriever_order(evals),
        "teacher": [r.teacher for r in evals],
        "student": [student_scores[f"{r.split}/{r.id}"] for r in evals],
        "student-int8": score(int8, evals, texts, batch_size=16),
    }
    report: dict[str, Any] = {"questions": len(evals), "sets": {}}
    for split in sorted({r.split for r in evals}):
        idx = [i for i, r in enumerate(evals) if r.split == split]
        part = [evals[i] for i in idx]
        report["sets"][split] = {"n": len(idx)} | {
            name: {k: round(v, 3) for k, v in rank_metrics(part, [s[i] for i in idx]).items()}
            for name, s in systems.items()
        }
    pooled = {name: per_question(evals, s) for name, s in systems.items()}
    report["pooled"] = {
        name: {m: round(statistics.fmean(q[m] for q in each), 3) for m in each[0]}
        for name, each in pooled.items()
    }
    for name in ("teacher", "student", "student-int8"):
        deltas = [
            c["MRR@10"] - d["MRR@10"] for c, d in zip(pooled[name], pooled["dense"], strict=True)
        ]
        est = bootstrap(deltas)
        report["pooled"][name]["MRR@10_vs_dense"] = [
            round(x, 3) for x in (est.mean, est.low, est.high)
        ]
    teacher_mrr = statistics.fmean(q["MRR@10"] for q in pooled["teacher"])
    report["retention"] = {
        name: round(statistics.fmean(q["MRR@10"] for q in pooled[name]) / teacher_mrr, 3)
        for name in ("student", "student-int8")
    }
    report["gate"] = {
        "min_retention": MIN_RETENTION,
        "passed": report["retention"]["student"] >= MIN_RETENTION,
        "int8_passed": report["retention"]["student-int8"] >= MIN_RETENTION,
        "teacher_beats_dense": report["pooled"]["teacher"]["MRR@10_vs_dense"][1] > 0,
    }
    sample = evals[:: max(len(evals) // latency_questions, 1)][:latency_questions]
    report["latency_cpu"] = {
        "questions": len(sample),
        "candidates": len(sample[0].articles),
        "student-int8": _latency(int8, sample, texts),
        "student": _latency(
            CrossEncoder(str(model_dir), max_length=512, device="cpu"), sample, texts
        ),
        "teacher": _latency(CrossEncoder(TEACHER, max_length=512, device="cpu"), sample, texts),
    }
    report["models"] = {"teacher": TEACHER, "student_base": STUDENT}
    return report


# Candidate base models for F8 (small, open, ungated; Llama and Gemma via unsloth's mirrors),
# plus the serving LLM and the embedding model as references.
TOKENIZERS = [
    "Qwen/Qwen2.5-1.5B-Instruct",
    "Qwen/Qwen3-1.7B",
    "Qwen/Qwen3.5-2B",
    "Qwen/Qwen3.5-4B",
    "unsloth/Llama-3.2-1B-Instruct",
    "unsloth/gemma-3-1b-it",
    "unsloth/gemma-3n-E2B-it",
    "openai/gpt-oss-20b",
    "BAAI/bge-m3",
]


def _tokenizers(names: list[str]) -> dict[str, Any]:
    from transformers import AutoTokenizer

    from mizan_train.tokenizer_stats import corpus_stats

    law = [t for _, t in current_articles(snapshot.read(DEFAULT_SNAPSHOT))]
    questions = protected_questions()
    report: dict[str, Any] = {
        "corpora": {
            "law": {"texts": len(law), "chars": sum(map(len, law))},
            "questions": {"texts": len(questions), "chars": sum(map(len, questions))},
        },
        "tokenizers": {},
    }
    for name in names:
        tokenizer = AutoTokenizer.from_pretrained(name)
        report["tokenizers"][name] = {
            "vocab": len(tokenizer),
            "law": corpus_stats(tokenizer, law),
            "questions": corpus_stats(tokenizer, questions),
        }
        log.info("tokenizer_stats", tokenizer=name, **report["tokenizers"][name]["law"])
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mizan-train")
    sub = parser.add_subparsers(dest="command", required=True)
    p_synth = sub.add_parser("synth", help="synthetic questions per article")
    p_synth.add_argument("--limit", type=int)
    # provider:model. Generators are used in order as daily free quotas run out; the judge is
    # another model family, so a model never checks its own questions.
    p_synth.add_argument(
        "--generators",
        nargs="+",
        default=[
            "gemini:gemini-3-flash-preview",
            "gemini:gemini-2.5-flash-lite",
            "gemini:gemini-2.5-flash",
        ],
    )
    p_synth.add_argument("--judge", default="groq:qwen/qwen3.8-27b")
    p_synth.add_argument("--pace", type=float, default=0.0)
    p_synth.add_argument("--batch", type=int, default=1, help="articles per request")
    p_build = sub.add_parser("build", help="split, leakage guard, hard negatives")
    p_build.add_argument("--negatives", type=int, default=5)
    p_embed = sub.add_parser("embed", help="fine-tune the embedding model")
    p_embed.add_argument("--name", required=True)
    p_embed.add_argument("--layers", type=int, default=DEFAULTS.trainable_layers)
    p_embed.add_argument("--lr", type=float, default=DEFAULTS.lr)
    p_embed.add_argument("--epochs", type=int, default=DEFAULTS.epochs)
    p_embed.add_argument("--batch-size", type=int, default=DEFAULTS.batch_size)
    p_cand = sub.add_parser("candidates", help="dense top-k per question for distillation")
    p_cand.add_argument("--k", type=int, default=20)
    p_va = sub.add_parser("verify-api", help="the API verifier on constructed claims")
    p_va.add_argument("--split", default="test")
    p_va.add_argument("--pace", type=float, default=2.5)
    p_vs = sub.add_parser("verify-synth", help="constructed verification claims")
    p_vs.add_argument("--limit", type=int)
    p_vs.add_argument(
        "--generators",
        nargs="+",
        default=[
            "gemini:gemini-3-flash-preview",
            "gemini:gemini-2.5-flash",
            "gemini:gemini-2.5-flash-lite",
        ],
    )
    # Another family than the generator, and than gpt-oss (the API verifier under test).
    p_vs.add_argument("--judge", default="groq:qwen/qwen3.8-27b")
    p_vs.add_argument("--pace", type=float, default=0.0)
    p_vs.add_argument("--batch", type=int, default=8)
    p_tok = sub.add_parser("tokenizers", help="token efficiency on Azerbaijani")
    p_tok.add_argument("--models", nargs="+", default=TOKENIZERS)
    p_tok.add_argument("--out", type=Path, default=Path("evals/results/f8_tokenizers/report.json"))
    p_rr = sub.add_parser("rerank-report", help="distilled reranker: quality and latency")
    p_rr.add_argument("--latency-questions", type=int, default=20)
    p_rr.add_argument("--out", type=Path, default=Path("evals/results/f7_rerank/report.json"))
    p_compare = sub.add_parser("compare", help="base vs fine-tuned retrieval")
    p_compare.add_argument("--base", default="bge-m3")
    p_compare.add_argument("--candidate", default="bge-m3-mizan")
    p_compare.add_argument("--out", type=Path, default=Path("evals/results/f7_embed/compare.json"))

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
    if args.command == "candidates":
        return asyncio.run(_candidates(args.k))
    if args.command == "verify-api":
        return asyncio.run(_verify_api(args.split, args.pace))
    if args.command == "verify-synth":
        return asyncio.run(
            _verify_synth(args.limit, args.generators, args.judge, args.pace, args.batch)
        )
    if args.command == "tokenizers":
        report = _tokenizers(args.models)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report, indent=1, ensure_ascii=False))
        return 0
    if args.command == "rerank-report":
        report = _rerank_report(args.latency_questions)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(json.dumps(report, indent=1))
        return 0 if report["gate"]["passed"] else 1
    if args.command == "compare":
        report = asyncio.run(_compare(args.base, args.candidate))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(json.dumps(report, indent=1))
        return 0 if report["gate"]["passed"] else 1
    if args.command == "synth":
        return asyncio.run(_synth(args.limit, args.generators, args.judge, args.pace, args.batch))
    return asyncio.run(_build(args.negatives))


if __name__ == "__main__":
    raise SystemExit(main())
