"""Citation extraction, checked against sentences taken from the Labour Code."""

import pytest

from mizan_graph.citations import SELF, extract

LABELLED = [
    (
        "Əmək müqaviləsinə bu Məcəllənin 70-ci maddəsinin “a” bəndi ilə xitam verildikdə",
        [(SELF, "70")],
    ),
    (
        "1. Əmək müqaviləsinə bu Məcəllənin 68, 69, 70, 73, 74 və 75-ci maddələrində "
        "nəzərdə tutulmuş əsaslarla xitam verilir.",
        [(SELF, n) for n in ("68", "69", "70", "73", "74", "75")],
    ),
    (
        "bu Məcəllənin 115–117 və 118-1-ci maddələrində nəzərdə tutulan məzuniyyətlər",
        [(SELF, "115"), (SELF, "116"), (SELF, "117"), (SELF, "118-1")],
    ),
    (
        "2. Bu Məcəllənin 91-ci, 92-ci və 93-cü maddələri ilə müəyyən edilmiş qaydalar",
        [(SELF, "91"), (SELF, "92"), (SELF, "93")],
    ),
    (
        "10-1.7. Bu Məcəllənin 10-1.2-ci və ya 10-1.3-cü maddələrinə əsasən",
        [(SELF, "10-1.2"), (SELF, "10-1.3")],
    ),
    (
        "İşəgötürənlər Azərbaycan Respublikasının Miqrasiya Məcəlləsinin 64-cü maddəsində "
        "nəzərdə tutulmuş hallarda",
        [("miqrasiya məcəllə", "64")],
    ),
    (
        "hüquqi şəxs olduqda onun Azərbaycan Respublikası Vergi Məcəlləsinin 33.2-ci maddəsinə "
        "uyğun olaraq",
        [("vergi məcəllə", "33.2")],
    ),
    (
        "“Mediasiya haqqında” Azərbaycan Respublikası Qanununun 27-ci maddəsinə uyğun olaraq",
        [("qanun: mediasiya haqqında", "27")],
    ),
    (
        # The second citation names no act: it continues the sentence's act (the Constitution).
        "Azərbaycan Respublikası Konstitusiyasının 25-ci maddəsinin I hissəsinə və 149-cu "
        "maddəsinin II hissəsinə uyğun olmadığına görə",
        [("konstitusiya", "25"), ("konstitusiya", "149")],
    ),
    (
        # ...but a new sentence starts over with this act.
        "Konstitusiyasının 148-ci maddəsi ilə müəyyən edilmişdir. Bu hallarda 72-ci maddə "
        "tətbiq edilir.",
        [("konstitusiya", "148"), (SELF, "72")],
    ),
    (
        "Bu Əmək müqaviləsinə Əmək Məcəlləsinin 68, 69 və 70-ci maddələrində göstərilən əsaslarla",
        [(SELF, "68"), (SELF, "69"), (SELF, "70")],
    ),
    (
        "bayram günləri işə cəlb edilən işçilərin əməyi 164-cü maddədə müəyyən edilmiş qaydada",
        [(SELF, "164")],
    ),
    (
        "Bu Qanunun 10-1.2-ci maddəsində nəzərdə tutulmuş müddətdən artıq",
        [(SELF, "10-1.2")],
    ),
]

MORE_LABELLED = [
    (
        # The act is named earlier in the sentence, not right before the number.
        "Azərbaycan Respublikası Konstitusiyasının əmək müqaviləsinin məcburi qaydada "
        "bağlanılmasının yolverilməzliyini təsbit edən 35-ci maddəsi Azərbaycan Respublikası "
        "Əmək Məcəlləsinin 7-ci maddəsinin 2-3 və 2-4-cü hissələrinə əsasən",
        [("konstitusiya", "35"), (SELF, "7")],
    ),
    (
        # The court is not the act.
        "Konstitusiya Məhkəməsinin Plenumu bu Qərarın 4-cü maddəsini tətbiq edir",
        [(SELF, "4")],
    ),
]
LABELLED += MORE_LABELLED

NOT_CITATIONS = [
    "2. Bu maddənin birinci hissəsində nəzərdə tutulmuş hallarda",  # intra-article
    "(Azərbaycan Respublikasının Qanunvericilik Toplusu, 1999-cu il, № 4, maddə 213)",
    "Maddə 70. İşəgötürən tərəfindən əmək müqaviləsinə xitam verilməsinin əsasları",
    "Bu Məcəllənin 7-ci maddəsinin 2-1-ci hissəsi",  # one citation; the part is not an article
]


@pytest.mark.parametrize(("text", "expected"), LABELLED)
def test_labelled_sentences(text: str, expected: list[tuple[str, str]]) -> None:
    assert [(c.act, c.number) for c in extract(text)] == expected


@pytest.mark.parametrize("text", NOT_CITATIONS[:3])
def test_not_citations(text: str) -> None:
    assert extract(text) == []


def test_parts_are_not_articles() -> None:
    assert [c.number for c in extract(NOT_CITATIONS[3])] == ["7"]


def test_dotted_numbers_cite_a_point_of_an_article() -> None:
    (citation,) = extract("Bu Məcəllənin 76-1.1-ci maddəsinə uyğun olaraq")
    assert (citation.number, citation.article) == ("76-1.1", "76-1")


def test_span_covers_the_citation() -> None:
    text = "İşçi bu Məcəllənin 120-ci maddəsində göstərilən hallarda"
    (citation,) = extract(text)
    assert text[citation.start : citation.end] == "120-ci maddəsində"
