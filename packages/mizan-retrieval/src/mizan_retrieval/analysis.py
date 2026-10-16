"""Lexical analysis of Azerbaijani for BM25.

Azerbaijani is agglutinative: "müqavilə", "müqaviləsinə", "müqavilələrin" are one word to a
reader and three unrelated terms to BM25. Full morphological analysis is heavy and there is no
maintained Azerbaijani stemmer; for Turkish (same family, same suffix system) IR studies found
that simply keeping the first ~5 characters of each word ("prefix truncation") matches far more
complex stemmers. Which length works best here is measured on the eval set, not assumed:
``Analyzer(prefix=None)`` is the unstemmed baseline.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from mizan_ingestion.text import az_lower, normalize

_TOKEN = re.compile(r"[a-zçəğıöşü]+|[0-9]+")

# Function words and question particles. Kept short on purpose: words like "az" (less) or
# "yaş" (age) look like noise but carry legal meaning ("yaşı 18-dən az olan").
STOPWORDS = frozenset(
    [
        "və",
        "ya",
        "yaxud",
        "həm",
        "ilə",
        "üçün",
        "bu",
        "o",
        "bir",
        "da",
        "də",
        "ki",
        "isə",
        "kimi",
        "belə",
        "həmin",
        "hər",
        "digər",
        "habelə",
        "həmçinin",
        "onun",
        "onların",
        "öz",
        "bütün",
        "əgər",
        "lakin",
        "ancaq",
        "amma",
        "görə",
        "qədər",
        "sonra",
        "əvvəl",
        "daha",
        "ən",
        "çox",
        "deyil",
        "hansı",
        "nə",
        "necə",
        "niyə",
        "kim",
        "mən",
        "sən",
        "biz",
        "siz",
        "onlar",
        "məni",
        "mənim",
        "mənə",
        "bizim",
        "olar",
        "olarmı",
        "bilər",
        "bilərmi",
        "bilərəmmi",
        "edir",
        "edilir",
        "olunur",
        "olur",
        "var",
        "varmı",
        "yoxsa",
        "mi",
        "mı",
        "mu",
        "mü",
    ]
)


@dataclass(frozen=True, slots=True)
class Analyzer:
    prefix: int | None = 5
    min_length: int = 2

    @property
    def name(self) -> str:
        return "bm25" if self.prefix is None else f"bm25_p{self.prefix}"

    def tokens(self, text: str) -> list[str]:
        out = []
        for token in _TOKEN.findall(az_lower(normalize(text))):
            if token in STOPWORDS or (len(token) < self.min_length and not token.isdigit()):
                continue
            out.append(token if self.prefix is None or token.isdigit() else token[: self.prefix])
        return out

    def term_frequencies(self, text: str) -> Counter[str]:
        return Counter(self.tokens(text))


ANALYZERS = {a.name: a for a in (Analyzer(None), Analyzer(5), Analyzer(6), Analyzer(7))}
