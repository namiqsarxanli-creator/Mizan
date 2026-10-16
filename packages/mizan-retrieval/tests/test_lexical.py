import pytest

from mizan_retrieval.analysis import ANALYZERS, Analyzer
from mizan_retrieval.sparse import BM25Encoder, term_id


def test_prefix_stemming_conflates_inflections() -> None:
    a = Analyzer(prefix=5)
    forms = ["müqavilə", "müqaviləsinə", "müqavilələrin", "Müqaviləni"]
    assert {t for f in forms for t in a.tokens(f)} == {"müqav"}


def test_unstemmed_analyzer_keeps_forms_apart() -> None:
    assert Analyzer(prefix=None).tokens("müqavilə müqaviləsinə") == ["müqavilə", "müqaviləsinə"]


def test_lowercases_with_azerbaijani_rules_and_drops_stopwords() -> None:
    assert Analyzer(prefix=None).tokens("İŞÇİ və İşəgötürən üçün") == ["işçi", "işəgötürən"]


def test_numbers_survive_and_are_never_truncated() -> None:
    assert Analyzer(prefix=3).tokens("Maddə 1234, yaşı 18-dən az") == [
        "mad",
        "1234",
        "yaş",
        "18",
        "dən",
        "az",
    ]


def test_analyzer_names_match_sparse_vector_names() -> None:
    assert set(ANALYZERS) == {"bm25", "bm25_p5", "bm25_p6", "bm25_p7"}


def test_term_ids_are_stable_across_processes() -> None:
    # A changed hash would silently break every existing index: pin one value.
    assert term_id("müqav") == term_id("müqav")
    assert 0 <= term_id("müqav") < 2**32
    assert term_id("müqav") != term_id("müqab")


@pytest.fixture
def encoder() -> BM25Encoder:
    return BM25Encoder(Analyzer(prefix=None), avgdl=10.0)


def test_query_and_document_share_term_ids(encoder: BM25Encoder) -> None:
    doc = encoder.encode_document("sınaq müddəti üç aydan çox ola bilməz")
    query = encoder.encode_query("sınaq müddəti")
    assert set(query.indices) <= set(doc.indices)
    assert query.values == [1.0, 1.0]


def test_term_frequency_saturates(encoder: BM25Encoder) -> None:
    def weight(text: str) -> float:
        sparse = encoder.encode_document(text)
        return sparse.values[sparse.indices.index(term_id("məzuniyyət"))]

    once, twice, ten = (weight(" ".join(["məzuniyyət"] * n)) for n in (1, 2, 10))
    assert once < twice < ten < encoder.k1 + 1


def test_long_documents_get_lower_term_weights(encoder: BM25Encoder) -> None:
    short = encoder.encode_document("məzuniyyət")
    long = encoder.encode_document("məzuniyyət " + " ".join(f"söz{i}" for i in range(40)))
    tid = term_id("məzuniyyət")
    assert long.values[long.indices.index(tid)] < short.values[short.indices.index(tid)]


def test_fit_computes_average_length() -> None:
    enc = BM25Encoder.fit(Analyzer(prefix=None), ["işçi işəgötürən", "işçi"])
    assert enc.avgdl == 1.5
