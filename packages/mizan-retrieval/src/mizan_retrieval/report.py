"""Write eval results: a human-readable Markdown report and machine-readable JSON."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from mizan_retrieval.evaluation import Question, RunResult

_MAIN_METRICS = ("Hit@1", "Hit@5", "Recall@5", "MRR@10", "nDCG@10")


def write_report(
    out_dir: Path, dataset: Path, questions: Sequence[Question], results: Sequence[RunResult]
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    by_type: dict[str, list[str]] = {}
    for q in questions:
        if q.relevant:  # out-of-scope questions have no retrieval metrics
            by_type.setdefault(q.type, []).append(q.id)

    lines = [
        "# Retrieval evaluation",
        "",
        f"- Dataset: `{dataset.as_posix()}` ({len(questions)} questions: "
        + ", ".join(f"{len(v)} {k}" for k, v in sorted(by_type.items()))
        + ")",
        f"- Generated: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "- Metrics at article level; `mean [95% bootstrap CI]`; latency is p50 on CPU.",
        "",
        "| Variant | " + " | ".join(_MAIN_METRICS) + " | p50 ms |",
        "|---|" + "---|" * (len(_MAIN_METRICS) + 1),
    ]
    for r in results:
        s = r.summary()
        cells = " | ".join(str(s[m]) for m in _MAIN_METRICS)
        lines.append(f"| {r.label} | {cells} | {r.p50_latency_ms:.0f} |")

    lines += ["", "## Recall@5 by question type", ""]
    types = sorted(by_type)
    lines += ["| Variant | " + " | ".join(types) + " |", "|---|" + "---|" * len(types)]
    for r in results:
        cells = " | ".join(f"{r.summary(by_type[t])['Recall@5'].mean:.2f}" for t in types)
        lines.append(f"| {r.label} | {cells} |")

    best = max(results, key=lambda r: r.summary()["Recall@5"].mean)
    misses = [
        q
        for q in questions
        if q.id in best.per_question and best.per_question[q.id]["Hit@5"] == 0.0
    ]
    lines += [
        "",
        f"## Misses of the best variant ({best.label}): Hit@5 = 0",
        "",
        *(f"- `{q.id}` {q.question} → expected {', '.join(sorted(q.relevant))}" for q in misses),
    ]
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "results.json").write_text(
        json.dumps(
            {
                r.label: {"per_question": r.per_question, "p50_latency_ms": r.p50_latency_ms}
                for r in results
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
