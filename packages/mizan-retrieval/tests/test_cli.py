from pathlib import Path

from mizan_retrieval.cli import _load_checkpoint, _save_checkpoint, _slug, default_variants
from mizan_retrieval.evaluation import RunResult


def test_variant_labels_give_unique_file_names() -> None:
    slugs = [_slug(v.label) for v in default_variants()]
    assert len(slugs) == len(set(slugs))
    assert _slug("bge-m3 + bm25_p5 + rerank@20 (article chunks)") == (
        "bge-m3-bm25-p5-rerank-20-article-chunks"
    )


def test_checkpoint_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "variants" / "x.json"
    result = RunResult("x", per_question={"q1": {"Hit@5": 1.0}}, latency_ms=[12.5])

    assert _load_checkpoint(path, "x") is None
    _save_checkpoint(path, result)
    loaded = _load_checkpoint(path, "x")

    assert loaded is not None
    assert loaded.per_question == result.per_question
    assert loaded.latency_ms == [12.5]
