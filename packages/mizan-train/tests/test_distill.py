"""Reranker distillation helpers (the training loop itself runs on a GPU, infra/gpu)."""

from pathlib import Path

import pytest

from mizan_train.distill import (
    Candidates,
    rank_metrics,
    read_candidates,
    retriever_order,
    score,
    write_candidates,
)


def _row(articles: list[str], gold: list[str]) -> Candidates:
    return Candidates("q", "sual", "val", gold, articles, [f"{a}|2026-01-01" for a in articles])


def test_rank_metrics_sorts_by_score() -> None:
    row = _row(["a", "b", "c"], ["c"])
    assert rank_metrics([row], [[0.1, 0.2, 0.9]]) == {"Hit@1": 1.0, "Recall@5": 1.0, "MRR@10": 1.0}
    assert rank_metrics([row], [[0.9, 0.2, 0.1]])["MRR@10"] == pytest.approx(1 / 3)


def test_rank_metrics_counts_every_gold_article_for_recall() -> None:
    row = _row([f"a{i}" for i in range(8)], ["a0", "a7"])
    metrics = rank_metrics([row], retriever_order([row]))
    assert metrics["Recall@5"] == 0.5  # a7 is sixth or later
    assert metrics["MRR@10"] == 1.0


def test_gold_outside_the_list_scores_zero() -> None:
    row = _row(["a", "b"], ["z"])
    assert rank_metrics([row], retriever_order([row])) == {
        "Hit@1": 0.0,
        "Recall@5": 0.0,
        "MRR@10": 0.0,
    }


def test_retriever_order_keeps_the_dense_ranking() -> None:
    row = _row(["x", "y", "z"], ["x"])
    assert retriever_order([row]) == [[0.0, -1.0, -2.0]]


def test_score_groups_pairs_per_question() -> None:
    class Fake:
        def predict(self, pairs: list[tuple[str, str]], **_: object) -> list[float]:
            return [float(len(p)) for _, p in pairs]

    rows = [_row(["a"], ["a"]), _row(["b", "cc"], ["b"])]
    texts = {"a|2026-01-01": "1", "b|2026-01-01": "22", "cc|2026-01-01": "333"}
    assert score(Fake(), rows, texts) == [[1.0], [2.0, 3.0]]


def test_candidates_round_trip(tmp_path: Path) -> None:
    rows = [_row(["a", "b"], ["b"])]
    rows[0].teacher = [0.5, 1.5]
    write_candidates(tmp_path / "c.jsonl", rows)
    assert read_candidates(tmp_path / "c.jsonl") == rows
