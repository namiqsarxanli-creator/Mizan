"""Constitutional Court decisions: splitting the text and classifying the outcome."""

import pytest
from word_fixtures import centered, document, encode, para

from mizan_ingestion.decisions import Outcome, classify, parse_decision
from mizan_ingestion.sources.eqanun import SourceError

TITLE = (
    "Azərbaycan Respublikası Əmək Məcəlləsinin 172-ci maddəsinin 5-ci hissəsinin "
    "şərh edilməsinə dair"
)


def decision(*operative: str) -> bytes:
    return encode(
        document(
            centered("Azərbaycan Respublikası adından"),
            centered(TITLE),
            para("Konstitusiya Məhkəməsinin Plenumu ..."),
            centered("MÜƏYYƏN ETDİ:"),
            para("Müraciətçi bildirir ki, ..."),
            # The reasoning may quote an operative heading; the last one counts.
            para("QƏRARA ALDI:"),
            para("Plenumun əvvəlki qərarında deyilir ..."),
            centered("QƏRARA ALDI:"),
            *(para(p) for p in operative),
            para("Sədr: Fərhad ABDULLAYEV"),
            para("Bakı şəhəri, 11 aprel 2023-cü il."),
        )
    )


def test_reasoning_and_operative_parts_are_split() -> None:
    parsed = parse_decision(
        decision("1. Ödənc fərdi əmək mübahisəsinin yarandığı hala da şamil edilir.", "2. Son."),
        title=TITLE,
    )
    assert parsed.reasoning.startswith("Müraciətçi bildirir")
    assert "Plenumun əvvəlki qərarında" in parsed.reasoning
    assert parsed.operative == (
        "1. Ödənc fərdi əmək mübahisəsinin yarandığı hala da şamil edilir.\n2. Son."
    )
    assert parsed.outcome is Outcome.INTERPRETATION


def test_a_decision_without_an_operative_part_is_rejected() -> None:
    raw = encode(document(centered(TITLE), para("mətn")))
    with pytest.raises(SourceError, match="operative"):
        parse_decision(raw, title=TITLE)


@pytest.mark.parametrize(
    ("operative", "outcome"),
    [
        # 61412 (2026): Art. 144(3) suspended.
        (
            "... Konstitusiyasının 25-ci maddəsinin I və III hissələrinə uyğun olmadığından "
            "tətbiqi dayandırılsın.",
            Outcome.UNCONSTITUTIONAL,
        ),
        ("... uyğun olmadığına görə etibarsız hesab edilsin.", Outcome.UNCONSTITUTIONAL),
        # 56305 (2024): Art. 47 "f" upheld.
        (
            "... Konstitusiyasının 25-ci maddəsinin I hissəsinə və 35-ci maddəsinin I-IV və VIII "
            "hissələrinə uyğun hesab edilsin.",
            Outcome.CONSTITUTIONAL,
        ),
        ("Qərar dərc edildiyi gündən qüvvəyə minir.", Outcome.OTHER),
    ],
)
def test_outcome(operative: str, outcome: Outcome) -> None:
    assert classify("... uyğunluğunun yoxlanılmasına dair", operative) is outcome


def test_interpretation_comes_from_the_title() -> None:
    assert classify(TITLE, "1. Müddəa şamil edilir.") is Outcome.INTERPRETATION
