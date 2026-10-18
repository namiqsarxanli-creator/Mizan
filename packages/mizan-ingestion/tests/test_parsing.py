from datetime import date

import pytest
from word_fixtures import document, encode, labour_code_excerpt, para

from mizan_ingestion.models import AmendingAct, NodeKind, Provision
from mizan_ingestion.parsing import parse_document
from mizan_ingestion.parsing.encoding import decode_html
from mizan_ingestion.parsing.endnotes import parse_amending_act
from mizan_ingestion.parsing.structure import flatten
from mizan_ingestion.parsing.word_html import parse_word_html

AZ_SENTENCE = "İşçi işəgötürəni yazılı ərizə ilə xəbərdar edir: ğ ö ü ş ç ı"


# ------------------------------------------------------------------ encoding


@pytest.mark.parametrize("real_encoding", ["windows-1254", "utf-8"])
def test_decode_ignores_wrong_declared_charset(real_encoding: str) -> None:
    raw = encode(document(para(AZ_SENTENCE)), real_encoding)  # declares windows-1251

    decoded = decode_html(raw)

    assert decoded.encoding == real_encoding
    assert parse_word_html(decoded.text).paragraphs[0].text == AZ_SENTENCE


def test_decode_keeps_declared_charset_when_it_is_right() -> None:
    raw = encode(document(para(AZ_SENTENCE)), "windows-1251")  # letters become &#...; refs

    assert decode_html(raw).encoding == "windows-1251"


# ------------------------------------------------------------------ structure


@pytest.fixture(scope="module")
def provisions() -> dict[str, Provision]:
    parsed = parse_document(encode(labour_code_excerpt()))
    flat, warnings = flatten(parsed.root)
    assert not parsed.warnings
    assert not warnings
    return {p.key: p for p in flat}


def test_title_comes_from_the_document_not_word_metadata() -> None:
    assert parse_document(encode(labour_code_excerpt())).title == (
        "AZƏRBAYCAN RESPUBLİKASININ ƏMƏK MƏCƏLLƏSİ"
    )


def test_sections_and_chapters(provisions: dict[str, Provision]) -> None:
    assert provisions["sec:I"].heading == "Ümumi normalar"
    assert provisions["ch:1"].heading == "Əsas müddəalar"
    assert provisions["ch:1"].parent_key == "sec:I"
    assert provisions["ch:11"].heading == "Əmək müqaviləsinə xitam verilməsi"
    assert provisions["ch:11"].parent_key == "sec:III"


def test_article_keys_do_not_depend_on_chapters(provisions: dict[str, Provision]) -> None:
    art = provisions["art:69"]
    assert art.parent_key == "ch:11"
    assert art.heading == "Əmək müqaviləsinə işçinin təşəbbüsü ilə xitam verilməsi"
    assert art.endnote_refs == (204,)


def test_unnumbered_lines_continue_the_current_node(provisions: dict[str, Provision]) -> None:
    assert provisions["art:1"].text == "Əmək qanunvericiliyi sistemi:\nbu Məcəllədən ibarətdir."


def test_numbered_hierarchy(provisions: dict[str, Provision]) -> None:
    assert provisions["art:69/part:1"].text.startswith("İşçi bir təqvim ayı")
    assert provisions["art:69/part:3"].endnote_refs == (206,)
    assert provisions["art:69/part:3/pt:3.1"].text == "təhsilini davam etdirdikdə;"
    assert provisions["art:69/part:3/pt:3.1/spt:3.1.1"].kind is NodeKind.SUBPOINT
    assert provisions["art:69/part:3/pt:3.2"].parent_key == "art:69/part:3"
    assert provisions["art:69/part:3-1"].parent_key == "art:69"


def test_items_and_trailing_text_attach_to_the_open_node(provisions: dict[str, Provision]) -> None:
    assert provisions["art:69/part:3-1/item:a"].text == "birinci hal;"
    assert provisions["art:69/part:3-1/item:b"].text == (
        "ikinci hal.\nBu hissə ilə müəyyən edilmiş qaydalar tətbiq edilir."
    )


def test_h3_article_headings_are_recognised(provisions: dict[str, Provision]) -> None:
    assert provisions["art:69-1"].heading == "Əlavə maddə"
    assert provisions["art:69-1"].text == "Mətn."


def test_annexes_are_split_from_the_last_article(provisions: dict[str, Provision]) -> None:
    assert "ƏLAVƏLƏR" not in provisions["art:69-1"].text
    assert "Məcəlləsinə" not in provisions["art:69-1"].text
    annex = provisions["annex:1"]
    assert annex.heading == "Əmək müqaviləsinin nümunəvi forması"
    part = provisions["annex:1/part:1"]
    assert part.heading == "Tərəflər haqqında məlumatlar"
    # Form fields inside annexes are text, not legal structure.
    assert part.text == "1.1. İşəgötürən ________\n1.2. İşçi ________"


def test_source_list_after_the_annexes_is_not_law_text(provisions: dict[str, Provision]) -> None:
    assert not any("88-IIQD" in p.text or "SİYAHISI" in p.text for p in provisions.values())


def test_provision_keys_are_unique_and_ordered(provisions: dict[str, Provision]) -> None:
    ordinals = [p.ordinal for p in provisions.values()]
    assert ordinals == sorted(ordinals) == list(range(len(ordinals)))


def test_content_hash_ignores_position_but_not_text() -> None:
    a = Provision("art:1", None, NodeKind.ARTICLE, "1", "H", "T", 0, 0, ())
    moved = Provision("art:1", "ch:2", NodeKind.ARTICLE, "1", "H", "T", 7, 1, (5,))
    edited = Provision("art:1", None, NodeKind.ARTICLE, "1", "H", "T!", 0, 0, ())
    assert a.content_hash == moved.content_hash != edited.content_hash


def test_warns_about_dangling_endnote_references() -> None:
    parsed = parse_document(encode(document(para("x", edn=7))))
    assert any("without a body" in w for w in parsed.warnings)


# ------------------------------------------------------------------ endnotes


@pytest.mark.parametrize(
    ("text", "act"),
    [
        (
            "30 dekabr 2025-ci il tarixli 332-VIIQD nömrəli Qanun",
            AmendingAct("332-VIIQD", date(2025, 12, 30)),
        ),
        ("1 fevral 1999-cu il 618-IQ nömrəli Qanun", AmendingAct("618-IQ", date(1999, 2, 1))),
        (
            "16 dekabr 2008-ci il tarixli, 743-IIIQD nömrəli Qanun",
            AmendingAct("743-IIIQD", date(2008, 12, 16)),
        ),
        (
            "16 oktyabr 2012-ci il tarixli 446-IVQD Qanunu",
            AmendingAct("446-IVQD", date(2012, 10, 16)),
        ),
        ("22 dekabr 2023-cü il tarixli nömrəli Qanun", AmendingAct(None, date(2023, 12, 22))),
    ],
)
def test_parse_amending_act(text: str, act: AmendingAct) -> None:
    assert parse_amending_act(text) == act


@pytest.mark.parametrize("text", ["Qanun ilə dəyişdirilmişdir", "31 fevral 2020-ci il tarixli"])
def test_parse_amending_act_rejects_garbage(text: str) -> None:
    assert parse_amending_act(text) is None
