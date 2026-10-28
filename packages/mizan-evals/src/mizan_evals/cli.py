"""Generation eval command line.

mizan-eval run    [--sample 60] [--pace 45]   retrieve -> answer -> judge (resumable)
mizan-eval report                             metrics with 95% CIs -> report.md / summary.json
mizan-eval label  --labeller NAME             blind human labelling (resumable)
mizan-eval kappa                              judge vs human agreement (Cohen's kappa)
mizan-eval agent  [--sample 40] [--extra ...] multi-agent vs baseline RAG (F5, resumable)
mizan-eval drift  [--webhook URL]             online faithfulness drift / SLO check (exit 1)
mizan-eval cache                              semantic cache threshold and saving (F9)
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import groq
import httpx
from neo4j import AsyncGraphDatabase
from qdrant_client import AsyncQdrantClient

from mizan_agents.gateway import LLMGateway
from mizan_agents.orchestrator import Orchestrator
from mizan_agents.schemas import Verdict
from mizan_agents.tools import LegalTools
from mizan_core.config import get_settings
from mizan_core.logging import configure_logging
from mizan_evals.agent_eval import (
    FaithfulnessJudge,
    SystemOutput,
    run_agent_eval,
    summarise_agent_eval,
)
from mizan_evals.cache_eval import (
    PARAPHRASE_PROMPT,
    Pair,
    half,
    near_misses,
    parse_paraphrases,
    pick_threshold,
    rates,
    simulate,
)
from mizan_evals.drift import daily_scores, detect_drift
from mizan_evals.judge import DEFAULT_JUDGE_MODEL, GroqJudge
from mizan_evals.labels import agreement, label_session, load_labels
from mizan_evals.run import Record, load_records, run_generation_eval, stratified_sample, summarise
from mizan_graph.decisions import attach_decisions, decision_hit
from mizan_graph.search import SERVING_POLICY, GraphSearcher
from mizan_graph.store import Neo4jGraph
from mizan_ingestion.db import repository as repo
from mizan_ingestion.db.session import create_engine, session_factory
from mizan_ingestion.sources.eqanun import SOURCE
from mizan_retrieval.answer import GroqAnswerer, fit_to_budget
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


async def _agent_eval(
    dataset: Path, extra: list[Path], out: Path, sample: int, pace_s: float
) -> int:
    """Agent vs baseline RAG (the serving /v1/ask pipeline) on the same questions."""
    settings = get_settings()
    if settings.llm.groq_api_key is None:
        print("the agent eval needs MIZAN_LLM__GROQ_API_KEY", file=sys.stderr)
        return 2
    questions = load_dataset(dataset)
    if sample:
        questions = stratified_sample(questions, sample)
    expected: dict[str, str] = {}
    for path in extra:
        questions += load_dataset(path)
        expected |= _expected(path)
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
    source_id = settings.retrieval.source_id
    try:
        dense = Searcher(qdrant, {config.dense_model: Embedder(MODELS[config.dense_model], cache)})
        searcher = GraphSearcher(dense, store, SERVING_POLICY)
        answerer = GroqAnswerer(
            client,
            model=settings.llm.groq_model,
            reasoning_effort=settings.llm.effort,
            max_context_chars=settings.llm.max_context_chars,
        )

        async def to_articles(hits: Sequence[Hit], sid: int, on: date) -> list[Hit]:
            return await expand_to_articles(qdrant, hits, source_id=sid, as_of=on)

        tools = LegalTools(searcher, config, to_articles, store, store, source_id)
        agent = Orchestrator(LLMGateway(client), tools)

        async def baseline(q: Question) -> SystemOutput:
            hits = await searcher.search(
                q.question, source_id=source_id, as_of=q.as_of, config=config, k=6
            )
            hits = await to_articles(hits, source_id, q.as_of)
            hits = await attach_decisions(hits, store, source_id=source_id, as_of=q.as_of)
            answer = await answerer.answer(q.question, hits, as_of=q.as_of)
            given = fit_to_budget(hits, settings.llm.max_context_chars)
            return SystemOutput(
                answer=answer.text,
                abstained=answer.refused,
                citations_total=len(answer.citations) + answer.rejected_citations,
                citations_verbatim=len(answer.citations),
                sources=[(h.key, h.text) for h in given],
                tokens=sum(answer.usage.values()),
                claims=[
                    {"source": c.key, "quote": c.quote, "verbatim": True} for c in answer.citations
                ],
            )

        async def multi_agent(q: Question) -> SystemOutput:
            result = await agent.run(q.question, as_of=q.as_of)
            return SystemOutput(
                answer=result.answer,
                abstained=result.verdict is Verdict.ABSTAIN,
                citations_total=len(result.claims),
                citations_verbatim=sum(c.quote_found for c in result.claims),
                sources=[(e.key, e.text) for e in result.evidence],
                confidence=result.confidence,
                tokens=result.usage.get("total", 0),
                claims=[
                    {
                        "source": c.claim.source,
                        "quote": c.claim.quote,
                        "verbatim": c.quote_found,
                        "support": c.support.value,
                    }
                    for c in result.claims
                ],
            )

        async def gold(q: Question) -> str:
            parts = [await _gold_texts(sorted(q.relevant), q.as_of)]
            if q.type == "court_decision":
                found = await store.decisions_for(source_id, sorted(q.relevant), as_of=q.as_of)
                parts += [decision_hit(d).text for d in found]
                if q.id in expected:
                    parts.append(f"Expected conclusion: {expected[q.id]}")
            return "\n\n".join(p for p in parts if p)

        records = await run_agent_eval(
            questions,
            {"baseline": baseline, "agent": multi_agent},
            judge=GroqJudge(client, model=DEFAULT_JUDGE_MODEL),
            faithfulness=FaithfulnessJudge(client),
            gold=gold,
            out=out / "records.jsonl",
            pace_s=pace_s,
        )
    finally:
        await driver.close()
        await client.close()
        await qdrant.close()
        cache.close()
    chosen = {q.id for q in questions}
    summary = summarise_agent_eval(
        [r for r in records.values() if r.id in chosen], ["baseline", "agent"]
    )
    text = json.dumps(summary, ensure_ascii=False, indent=1)
    (out / "summary.json").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


async def _drift(webhook: str | None, today: date | None) -> int:
    """Exit 1 on an alert (cron and CI friendly); post the message to ``webhook`` if given."""
    engine = create_engine(get_settings().postgres)
    on = today or date.today()  # noqa: DTZ011 - the server's date is the day of the check
    try:
        scores = await daily_scores(session_factory(engine), since=on - timedelta(days=40))
    finally:
        await engine.dispose()
    report = detect_drift(scores, today=on)
    print(json.dumps({"status": report.status, "message": report.message()}, ensure_ascii=False))
    if report.alert and webhook:
        # Slack-compatible payload; most chat webhooks accept {"text": ...}.
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(webhook, json={"text": f"Mizan: {report.message()}"})
    return 1 if report.alert else 0


PARAPHRASES = Path("evals/datasets/v1/paraphrases.jsonl")


def _read_paraphrases() -> dict[str, list[str]]:
    if not PARAPHRASES.exists():
        return {}
    rows = [json.loads(line) for line in PARAPHRASES.read_text(encoding="utf-8").splitlines()]
    return {r["id"]: r["paraphrases"] for r in rows}


def _append_paraphrases(rows: list[dict[str, Any]]) -> None:
    with PARAPHRASES.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


async def _paraphrases(
    questions: list[Question], n: int = 2, batch: int = 20
) -> dict[str, list[str]]:
    """Two paraphrases per question, generated once and kept as a dataset file."""
    done = await asyncio.to_thread(_read_paraphrases)
    todo = [q for q in questions if q.id not in done]
    if not todo:
        return done
    settings = get_settings()
    if settings.llm.groq_api_key is None:
        raise SystemExit("paraphrases need MIZAN_LLM__GROQ_API_KEY")
    client = groq.AsyncGroq(api_key=settings.llm.groq_api_key.get_secret_value())
    try:
        for start in range(0, len(todo), batch):
            chunk = todo[start : start + batch]
            listing = "\n".join(f"[{q.id}] {q.question}" for q in chunk)
            response = await client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[
                    {"role": "user", "content": PARAPHRASE_PROMPT.format(n=n, questions=listing)}
                ],
                temperature=0.7,
                max_completion_tokens=6000,
                reasoning_effort="low",
            )
            got = parse_paraphrases(response.choices[0].message.content or "")
            rows: list[dict[str, Any]] = [
                {"id": q.id, "question": q.question, "paraphrases": got[q.id][:n]}
                for q in chunk
                if q.id in got
            ]
            await asyncio.to_thread(_append_paraphrases, rows)
            done |= {r["id"]: r["paraphrases"] for r in rows}
            await asyncio.sleep(3)
    finally:
        await client.close()
    return done


async def _cache() -> dict[str, Any]:
    settings = get_settings()
    questions = [q for q in load_dataset(DEFAULT_DATASET) if q.relevant]
    paraphrases = await _paraphrases(questions)
    questions = [q for q in questions if q.id in paraphrases]
    gold = {q.id: q.relevant for q in questions}
    texts = {q.id: q.question for q in questions}
    phrasings: dict[str, list[str]] = {}
    for q in questions:
        phrasings[q.id] = [q.id]
        for i, text in enumerate(paraphrases[q.id], start=1):
            texts[f"{q.id}#{i}"] = text
            phrasings[q.id].append(f"{q.id}#{i}")
    owner = {p: qid for qid, ps in phrasings.items() for p in ps}

    config = serving_variant()
    embedder = Embedder(
        MODELS[config.dense_model], EmbeddingCache(Path(settings.retrieval.embeddings_cache))
    )
    vectors = {k: embedder.embed_query(t) for k, t in texts.items()}

    def pairs(part: str) -> list[Pair]:
        mine = {q.id for q in questions if half(q.id) == part}
        positive = [
            Pair(qid, p, float(vectors[qid] @ vectors[p]), True)
            for qid in sorted(mine)
            for p in phrasings[qid][1:]
        ]
        negative = [
            Pair(a, b, float(vectors[a] @ vectors[b]), False)
            for a, b in near_misses({k: v for k, v in gold.items() if k in mine})
        ]
        return positive + negative

    calib, evaluation = pairs("calib"), pairs("eval")
    threshold = pick_threshold(calib)

    # The workload: every evaluation question in its three phrasings. "Retrieved again" is the
    # proxy for the evidence fingerprint: the earlier phrasing's gold articles are in this
    # phrasing's top-6 (the /v1/ask default k).
    eval_phrasings = {qid: ps for qid, ps in phrasings.items() if half(qid) == "eval"}
    key = settings.qdrant.api_key.get_secret_value() if settings.qdrant.api_key else None
    qdrant = AsyncQdrantClient(url=settings.qdrant.url, api_key=key, timeout=120)
    retrieved: dict[str, set[str]] = {}
    try:
        searcher = Searcher(qdrant, {config.dense_model: embedder})
        for ps in eval_phrasings.values():
            for p in ps:
                hits = await searcher.search(
                    texts[p], source_id=settings.retrieval.source_id, as_of=date(2026, 10, 13),
                    config=config, k=6,
                )  # fmt: skip
                retrieved[p] = {h.article_key for h in hits}
    finally:
        await qdrant.close()

    def still_retrieved(earlier: str, now: str) -> bool:
        return gold[owner[earlier]] <= retrieved[now]

    report: dict[str, Any] = {
        "questions": len(questions),
        "threshold": round(threshold, 4),
        "calibration": rates(calib, threshold),
        "evaluation": rates(evaluation, threshold),
        "workload": simulate(eval_phrasings, vectors, still_retrieved, threshold),
        "workload_similarity_only": simulate(eval_phrasings, vectors, lambda a, b: True, threshold),
    }
    report["gate"] = {"min_saving": 0.25, "passed": report["workload"]["saving"] >= 0.25}
    return report


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
    p_drift = sub.add_parser("drift", help="alert when online faithfulness drops (exit 1)")
    p_drift.add_argument("--webhook", help="post alerts to this chat webhook")
    p_drift.add_argument("--today", type=date.fromisoformat)

    p_agent = sub.add_parser("agent", help="multi-agent vs baseline RAG")
    p_agent.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    p_agent.add_argument(
        "--extra", type=Path, nargs="*", default=[Path("evals/datasets/v1/court_decisions.jsonl")]
    )
    p_agent.add_argument("--out", type=Path, default=Path("evals/results/f5_agent"))
    p_agent.add_argument("--sample", type=int, default=40)
    p_agent.add_argument("--pace", type=float, default=20.0)

    p_cache = sub.add_parser("cache", help="semantic cache threshold and saving")
    p_cache.add_argument("--out", type=Path, default=Path("evals/results/f9_cache/report.json"))
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
    if args.command == "drift":
        return asyncio.run(_drift(args.webhook, args.today))
    if args.command == "agent":
        return asyncio.run(_agent_eval(args.dataset, args.extra, args.out, args.sample, args.pace))
    if args.command == "cache":
        report = asyncio.run(_cache())
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(json.dumps(report, indent=1))
        return 0 if report["gate"]["passed"] else 1
    if args.command == "report":
        _report(args.out, list(load_records(args.out / "records.jsonl").values()))
        return 0
    if args.command == "label":
        return _label(args.out, args.labeller, args.limit)
    return _kappa(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
