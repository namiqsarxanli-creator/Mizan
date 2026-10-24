"""Provision diffs and amendment splitting."""

from datetime import date

from mizan_ingestion.diff import ChangeKind, Text, compare, mark_words
from mizan_ingestion.models import AmendingAct
from mizan_ingestion.parsing.endnotes import split_amendments


def test_word_diff_marks_only_what_changed() -> None:
    marked = mark_words(
        "İşçi əmək müqaviləsini ləğv edə bilər.", "İşçi əmək müqaviləsinə xitam verə bilər."
    )
    assert (
        marked
        == "İşçi əmək [-müqaviləsini-]{+müqaviləsinə+} [-ləğv-]{+xitam+} [-edə-]{+verə+} bilər."
    )


def test_word_diff_of_an_insertion() -> None:
    assert (
        mark_words("yazılı ərizəsi", "yazılı (elektron) ərizəsi") == "yazılı {+(elektron) +}ərizəsi"
    )


def test_compare_reports_added_changed_and_removed_provisions() -> None:
    before = [
        Text("art:69", "Ləğv qaydası", ""),
        Text("art:69/part:1", None, "bir ay"),
        Text("art:69/part:9", None, "köhnə hissə"),
    ]
    after = [
        Text("art:69", "Xitam qaydası", ""),
        Text("art:69/part:1", None, "bir ay"),
        Text("art:69/part:3-1", None, "yeni hissə"),
    ]

    changes = compare(before, after)

    assert [(c.key, c.kind) for c in changes] == [
        ("art:69", ChangeKind.CHANGED),
        ("art:69/part:3-1", ChangeKind.ADDED),
        ("art:69/part:9", ChangeKind.REMOVED),
    ]
    assert changes[0].marked == "[-Ləğv-]{+Xitam+} qaydası"


def test_identical_texts_have_no_changes() -> None:
    texts = [Text("art:1", "Başlıq", "mətn")]
    assert compare(texts, texts) == []


NOTE = (
    "28 iyun 2024-cü il tarixli 1195-VIQD nömrəli Azərbaycan Respublikasının Qanunu "
    "(rəsmi internet saytı, 12 avqust 2024-cü il) ilə 1-ci hissəyə sözlər əlavə edilmişdir. "
    "30 dekabr 2025-ci il tarixli 332-VIIQD nömrəli Azərbaycan Respublikasının Qanunu "
    "(“Azərbaycan” qəzeti, 17 yanvar 2026-cı il) ilə sözlər əvəz edilmişdir."
)


def test_an_endnote_with_two_amendments_is_split_at_each_act() -> None:
    parts = split_amendments(NOTE)

    assert [act for act, _ in parts] == [
        AmendingAct("1195-VIQD", date(2024, 6, 28)),
        AmendingAct("332-VIIQD", date(2025, 12, 30)),
    ]
    assert parts[0][1].endswith("əlavə edilmişdir.")
    assert parts[1][1].startswith("30 dekabr 2025")


def test_publication_dates_do_not_start_an_amendment() -> None:
    (only,) = split_amendments(NOTE.split(" 30 dekabr")[0])
    assert only[0].number == "1195-VIQD"
