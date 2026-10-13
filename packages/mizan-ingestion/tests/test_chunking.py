import pytest
from word_fixtures import encode, labour_code_excerpt

from mizan_ingestion.chunking import Strategy, article_chunks, fixed_chunks, part_chunks
from mizan_ingestion.models import Provision
from mizan_ingestion.parsing import parse_document
from mizan_ingestion.parsing.structure import flatten

TITLE = "Əmək Məcəlləsi"


@pytest.fixture(scope="module")
def provisions() -> list[Provision]:
    flat, _ = flatten(parse_document(encode(labour_code_excerpt())).root)
    return flat


def test_article_chunk_has_breadcrumb_and_whole_article(provisions: list[Provision]) -> None:
    chunks = {c.key: c for c in article_chunks(provisions, document_title=TITLE)}

    art69 = chunks["art:69"]
    first_line, *body = art69.text.splitlines()
    assert first_line == (
        "Əmək Məcəlləsi > III bölmə Əmək müqaviləsi > 11-ci fəsil Əmək müqaviləsinə xitam "
        "verilməsi > Maddə 69. Əmək müqaviləsinə işçinin təşəbbüsü ilə xitam verilməsi"
    )
    assert body[0].startswith("1. İşçi bir təqvim ayı")
    assert "3.1.1. əyani təhsil alanda;" in body
    assert "a) birinci hal;" in body
    assert "art:69/part:3/pt:3.1/spt:3.1.1" in art69.cites


def test_annexes_are_chunked_like_articles(provisions: list[Provision]) -> None:
    keys = [c.key for c in article_chunks(provisions, document_title=TITLE)]
    assert keys == ["art:1", "art:69", "art:69-1", "annex:1"]


def test_long_articles_fall_back_to_parts_with_a_parent(provisions: list[Provision]) -> None:
    chunks = article_chunks(provisions, document_title=TITLE, max_chars=200)
    art69 = [c for c in chunks if c.parent_key == "art:69"]

    assert [c.key for c in art69] == [
        "art:69/part:1",
        "art:69/part:2",
        "art:69/part:3",
        "art:69/part:3-1",
    ]
    assert all(c.text.startswith("Əmək Məcəlləsi > ") for c in art69)
    assert "3.1. təhsilini davam etdirdikdə;" in art69[2].text


def test_part_chunks(provisions: list[Provision]) -> None:
    chunks = part_chunks(provisions, document_title=TITLE)

    assert {c.strategy for c in chunks} == {Strategy.PART}
    # Article 1 has no parts: it is its own unit.
    assert next(c for c in chunks if c.key == "art:1").parent_key == "art:1"
    part3_1 = next(c for c in chunks if c.key == "art:69/part:3-1")
    assert part3_1.cites == (
        "art:69/part:3-1",
        "art:69/part:3-1/item:a",
        "art:69/part:3-1/item:b",
    )


def test_fixed_chunks_overlap_and_cover_everything(provisions: list[Provision]) -> None:
    chunks = fixed_chunks(provisions, size=300, overlap=50)

    assert len(chunks) > 2
    assert all(len(c.text) <= 300 for c in chunks)
    joined = " ".join(c.text for c in chunks)
    for phrase in ("Maddə 1.", "3-1. Diplomatik", "1-ci əlavə."):
        assert phrase in joined
    # Consecutive windows share text.
    assert chunks[0].text[-20:] in chunks[1].text


@pytest.mark.parametrize(("size", "overlap"), [(100, 100), (100, -1)])
def test_fixed_chunks_validate_overlap(
    provisions: list[Provision], size: int, overlap: int
) -> None:
    with pytest.raises(ValueError, match="overlap"):
        fixed_chunks(provisions, size=size, overlap=overlap)
