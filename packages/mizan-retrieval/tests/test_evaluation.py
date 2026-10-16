import json
from datetime import date
from pathlib import Path

import pytest

from mizan_retrieval.evaluation import (
    Question,
    RunResult,
    bootstrap,
    dedupe,
    hit_at,
    load_dataset,
    mrr_at,
    ndcg_at,
    recall_at,
)

REL = frozenset({"art:69", "art:70"})


def test_dedupe_keeps_first_occurrence() -> None:
    assert dedupe(["art:1", "art:69", "art:1", "art:2"]) == ["art:1", "art:69", "art:2"]


def test_rank_metrics() -> None:
    ranked = ["art:1", "art:69", "art:2", "art:3", "art:4", "art:70"]
    assert hit_at(ranked, REL, 1) == 0.0
    assert hit_at(ranked, REL, 5) == 1.0
    assert recall_at(ranked, REL, 5) == 0.5
    assert recall_at(ranked, REL, 10) == 1.0
    assert mrr_at(ranked, REL) == 0.5


def test_ndcg_is_one_for_a_perfect_ranking() -> None:
    assert ndcg_at(["art:69", "art:70", "art:1"], REL) == pytest.approx(1.0)
    assert ndcg_at(["art:1", "art:69", "art:70"], REL) < 1.0
    assert ndcg_at([], REL) == 0.0


def test_bootstrap_interval_brackets_the_mean_and_is_reproducible() -> None:
    values = [1.0] * 30 + [0.0] * 10
    first, second = bootstrap(values), bootstrap(values)
    assert first == second
    assert first.low <= first.mean == 0.75 <= first.high
    assert first.high - first.low < 0.35


def test_constant_values_have_a_degenerate_interval() -> None:
    est = bootstrap([1.0] * 20)
    assert (est.low, est.mean, est.high) == (1.0, 1.0, 1.0)


def test_run_result_scores_at_article_level() -> None:
    q = Question("q1", "?", frozenset({"art:69"}), "single", date(2026, 1, 1))
    result = RunResult("x")
    result.add(q, ["art:1", "art:1", "art:1", "art:1", "art:1", "art:69"], latency_ms=5.0)

    # Five chunks of art:1 count once, so art:69 is at article rank 2.
    assert result.per_question["q1"]["Hit@5"] == 1.0
    assert result.per_question["q1"]["MRR@10"] == 0.5


def test_load_dataset_defaults_as_of_to_today(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    rows = [
        {"id": "a", "question": "?", "relevant": ["art:1"], "type": "single"},
        {
            "id": "b",
            "question": "?",
            "relevant": ["art:2"],
            "type": "temporal",
            "as_of": "2010-01-01",
        },
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    a, b = load_dataset(path, today=date(2026, 10, 13))

    assert a.as_of == date(2026, 10, 13)
    assert b.as_of == date(2010, 1, 1)
    assert b.relevant == frozenset({"art:2"})


def test_shipped_dataset_is_well_formed() -> None:
    path = Path(__file__).resolve().parents[3] / "evals/datasets/v0/retrieval.jsonl"
    questions = load_dataset(path)
    assert len(questions) >= 50
    assert len({q.id for q in questions}) == len(questions)
    assert all(q.relevant and all(k.startswith("art:") for k in q.relevant) for q in questions)
