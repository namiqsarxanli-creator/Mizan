import pytest

from mizan_ingestion.text import (
    az_lower,
    normalize,
    ordinal_suffix,
    parse_ordinal,
    parse_roman,
)


def test_normalize_collapses_word_whitespace_and_composes() -> None:
    decomposed = "Madd\u0259\u00a0 1.\n  \u018f" + "e\u0301"  # NBSP, newline, combining accent
    assert normalize(decomposed) == "Maddə 1. Əé"


def test_normalize_strips_invisible_characters() -> None:
    assert normalize("i\u015f\u00adçi\u200b") == "işçi"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Respublİkasının", "Respublikasının"),
        ("Azərbaycan RespublİkasInIn", "Azərbaycan Respublikasının"),
        ("Məcəlləsİ", "Məcəlləsi"),
        ("332-VIIQD", "332-VIIQD"),  # capitals after capitals are left alone
        ("İşçi", "İşçi"),
    ],
)
def test_normalize_repairs_small_caps_artefacts(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_az_lower_respects_dotted_and_dotless_i() -> None:
    assert az_lower("İŞÇİ IŞIQ") == "işçi ışıq"


@pytest.mark.parametrize(
    ("phrase", "number"),
    [
        ("Birinci", 1),
        ("İkinci", 2),
        ("Üçüncü", 3),
        ("Dördüncü", 4),
        ("Altıncı", 6),
        ("Doqquzuncu", 9),
        ("Onuncu", 10),
        ("On birinci", 11),
        ("İyirminci", 20),
        ("İyirmi beşinci", 25),
        ("Otuzuncu", 30),
        ("Qırxıncı", 40),
        ("Qırx səkkizinci", 48),
        ("Əllinci", 50),
        ("Doxsan doqquzuncu", 99),
    ],
)
def test_parse_ordinal(phrase: str, number: int) -> None:
    assert parse_ordinal(phrase) == number


@pytest.mark.parametrize("phrase", ["", "Ümumi normalar", "bir", "On"])
def test_parse_ordinal_rejects_non_ordinals(phrase: str) -> None:
    assert parse_ordinal(phrase) is None


@pytest.mark.parametrize(
    ("numeral", "number"), [("I", 1), ("IV", 4), ("IX", 9), ("XIII", 13), ("XL", 40)]
)
def test_parse_roman(numeral: str, number: int) -> None:
    assert parse_roman(numeral) == number


@pytest.mark.parametrize("numeral", ["", "IIII", "VX", "ABC"])
def test_parse_roman_rejects_invalid(numeral: str) -> None:
    assert parse_roman(numeral) is None


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (1, "1-ci"), (2, "2-ci"), (3, "3-cü"), (4, "4-cü"), (5, "5-ci"), (6, "6-cı"),
        (7, "7-ci"), (8, "8-ci"), (9, "9-cu"), (10, "10-cu"), (11, "11-ci"), (20, "20-ci"),
        (30, "30-cu"), (40, "40-cı"), (48, "48-ci"), (50, "50-ci"), (60, "60-cı"),
        (69, "69-cu"), (90, "90-cı"), (100, "100-cü"), (317, "317-ci"),
    ],
)  # fmt: skip
def test_ordinal_suffix_follows_vowel_harmony(n: int, expected: str) -> None:
    assert ordinal_suffix(n) == expected
