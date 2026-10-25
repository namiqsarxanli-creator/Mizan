"""Training data: article-level split, leakage guard, hard negatives."""

from collections import Counter
from pathlib import Path

from mizan_train.dataset import (
    BuildReport,
    article_split,
    build_examples,
    hard_negatives,
    jaccard,
    leaks,
    read_examples,
    write_examples,
)
from mizan_train.synth import Pair


def test_split_is_stable_and_roughly_80_10_10() -> None:
    keys = [f"art:{n}" for n in range(1, 2001)]
    splits = Counter(article_split(k) for k in keys)
    assert [article_split(k) for k in keys[:50]] == [article_split(k) for k in keys[:50]]
    assert 0.75 < splits["train"] / 2000 < 0.85
    assert 0.07 < splits["test"] / 2000 < 0.13


def test_all_questions_about_one_article_share_its_split() -> None:
    pairs = [Pair(f"Sual {i} sınaq müddəti haqqında?", "art:51", "g", True) for i in range(4)]
    examples = build_examples(pairs, [])
    assert len({e.split for e in examples}) == 1


def test_near_duplicates_of_evaluation_questions_are_dropped() -> None:
    protected = ["İşdən çıxmaq istəyirəm, işəgötürənə neçə gün əvvəl xəbər verməliyəm?"]
    assert leaks("İşdən çıxmaq istəyirəm, işəgötürənə neçə gün əvvəl xəbər verim?", protected)
    assert not leaks("Sınaq müddəti ən çox nə qədər ola bilər?", protected)
    assert jaccard("abc", "abc") == 1.0

    report = BuildReport()
    pairs = [
        Pair(
            "İşdən çıxmaq istəyirəm, işəgötürənə neçə gün əvvəl xəbər verim?", "art:69", "g", True
        ),
        Pair("Sınaq müddəti ən çox nə qədər ola bilər?", "art:51", "g", True),
        Pair("Bu maddə nədən bəhs edir?", "art:51", "g", False),
    ]
    examples = build_examples(pairs, protected, report=report)
    assert [e.positive for e in examples] == ["art:51"]
    assert (report.pairs, report.rejected_by_judge, report.leaked) == (3, 1, 1)


def test_hard_negatives_skip_the_top_ranks_the_gold_and_its_citations() -> None:
    ranked = ["art:77", "art:70", "art:69", "art:71", "art:68", "art:83", "art:76", "art:300"]
    negatives = hard_negatives(ranked, "art:77", related={"art:68"}, start=2, k=3)
    assert negatives == ("art:69", "art:71", "art:83")


def test_examples_round_trip(tmp_path: Path) -> None:
    examples = build_examples([Pair("Məzuniyyət neçə gündür?", "art:114", "g", True)], [])
    write_examples(tmp_path / "x.jsonl", examples)
    assert read_examples(tmp_path / "x.jsonl") == examples
