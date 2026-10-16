"""BM25 as Qdrant sparse vectors.

Document side: each term's BM25 term-frequency component,
``tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))``. Query side: weight 1 per distinct
term. Qdrant's ``Modifier.IDF`` multiplies by the IDF of each term over the collection at query
time, so the dot product is exactly BM25 and IDF stays correct as documents are added.

Terms map to 32-bit ids through a stable hash, so no vocabulary has to be stored or shared
between the indexer and the API.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from mizan_retrieval.analysis import Analyzer


def term_id(term: str) -> int:
    return int.from_bytes(hashlib.blake2b(term.encode("utf-8"), digest_size=4).digest(), "little")


@dataclass(frozen=True, slots=True)
class Sparse:
    indices: list[int]
    values: list[float]


@dataclass(frozen=True, slots=True)
class BM25Encoder:
    analyzer: Analyzer
    avgdl: float
    k1: float = 1.2
    b: float = 0.75

    @classmethod
    def fit(cls, analyzer: Analyzer, corpus: Iterable[str], **params: float) -> BM25Encoder:
        lengths = [len(analyzer.tokens(text)) for text in corpus]
        avgdl = sum(lengths) / len(lengths) if lengths else 1.0
        return cls(analyzer, max(avgdl, 1.0), **params)

    def encode_document(self, text: str) -> Sparse:
        tf = self.analyzer.term_frequencies(text)
        dl = sum(tf.values())
        norm = self.k1 * (1 - self.b + self.b * dl / self.avgdl)
        weights: dict[int, float] = {}
        for term, count in tf.items():
            # Hash collisions are astronomically rare at our vocabulary size; summing keeps them
            # harmless when they happen.
            idx = term_id(term)
            weights[idx] = weights.get(idx, 0.0) + count * (self.k1 + 1) / (count + norm)
        return Sparse(list(weights), list(weights.values()))

    def encode_query(self, text: str) -> Sparse:
        ids = sorted({term_id(t) for t in self.analyzer.tokens(text)})
        return Sparse(ids, [1.0] * len(ids))
