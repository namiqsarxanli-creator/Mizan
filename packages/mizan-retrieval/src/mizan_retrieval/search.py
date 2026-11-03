"""Temporal hybrid search.

    dense (bge-m3 / e5) ──┐
                          ├─ RRF fusion ─ (optional) cross-encoder rerank ─ hits
    sparse (BM25)      ───┘
    every branch filtered to chunks valid on `as_of`

Reciprocal Rank Fusion combines rankings, not scores, so cosine similarities and BM25 scores
never have to be calibrated against each other.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol

from qdrant_client import AsyncQdrantClient, models

from mizan_ingestion.chunking import Strategy
from mizan_retrieval.embeddings import Embedder, QueryBatcher
from mizan_retrieval.index import alias_for, bm25_encoders_for_query
from mizan_retrieval.rerank import Reranker


class Mode(StrEnum):
    DENSE = "dense"
    SPARSE = "sparse"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class SearchConfig:
    mode: Mode = Mode.HYBRID
    dense_model: str = "bge-m3"
    sparse: str = "bm25_p5"
    strategy: Strategy = Strategy.ARTICLE
    candidates: int = 50
    rerank_top: int = 0  # 0 = no reranking
    # Eval-only marker: the query was expanded by an LLM rewrite before searching.
    rewrite: bool = False

    @property
    def label(self) -> str:
        parts = {
            Mode.DENSE: [self.dense_model],
            Mode.SPARSE: [self.sparse],
            Mode.HYBRID: [self.dense_model, "+", self.sparse],
        }[self.mode]
        rerank = f" + rerank@{self.rerank_top}" if self.rerank_top else ""
        rewrite = " + rewrite" if self.rewrite else ""
        return f"{' '.join(parts)}{rerank}{rewrite} ({self.strategy.value} chunks)"


@dataclass(frozen=True, slots=True)
class Hit:
    key: str
    article_key: str
    parent_key: str | None
    text: str
    score: float
    valid_from: date
    cites: tuple[str, ...]


class SearchBackend(Protocol):
    async def search(
        self,
        query: str,
        *,
        source_id: int,
        as_of: date,
        config: SearchConfig,
        k: int = 10,
        only_articles: Collection[str] | None = None,
    ) -> list[Hit]: ...


def temporal_filter(source_id: int, on: date, *extra: models.Condition) -> models.Filter:
    day = on.toordinal()
    return models.Filter(
        must=[
            models.FieldCondition(key="source_id", match=models.MatchValue(value=source_id)),
            models.FieldCondition(key="valid_from", range=models.Range(lte=day)),
            models.FieldCondition(key="valid_to", range=models.Range(gt=day)),
            *extra,
        ]
    )


class Searcher:
    def __init__(
        self,
        client: AsyncQdrantClient,
        embedders: dict[str, Embedder],
        reranker: Reranker | None = None,
        batchers: dict[str, QueryBatcher] | None = None,
    ) -> None:
        self._client = client
        self._embedders = embedders
        self._reranker = reranker
        # Optional micro-batching of query embeddings, per dense model (F11 load test).
        self._batchers = batchers or {}
        self._encoders = bm25_encoders_for_query()

    async def search(
        self,
        query: str,
        *,
        source_id: int,
        as_of: date,
        config: SearchConfig,
        k: int = 10,
        only_articles: Collection[str] | None = None,
    ) -> list[Hit]:
        """Rank chunks valid on ``as_of``; ``only_articles`` restricts ranking to those articles."""
        extra = (
            [
                models.FieldCondition(
                    key="article_key", match=models.MatchAny(any=list(only_articles))
                )
            ]
            if only_articles is not None
            else []
        )
        flt = temporal_filter(source_id, as_of, *extra)
        limit = max(k, config.candidates, config.rerank_top)
        prefetch: list[models.Prefetch] = []

        if config.mode in (Mode.DENSE, Mode.HYBRID):
            batcher = self._batchers.get(config.dense_model)
            if batcher is not None:
                vector = await batcher.embed_query(query)
            else:
                embedder = self._embedders[config.dense_model]
                vector = await asyncio.to_thread(embedder.embed_query, query)
            prefetch.append(
                models.Prefetch(
                    query=vector.tolist(), using=config.dense_model, filter=flt, limit=limit
                )
            )
        if config.mode in (Mode.SPARSE, Mode.HYBRID):
            sparse = self._encoders[config.sparse].encode_query(query)
            if sparse.indices:  # a query of only stopwords has no lexical signal
                prefetch.append(
                    models.Prefetch(
                        query=models.SparseVector(indices=sparse.indices, values=sparse.values),
                        using=config.sparse,
                        filter=flt,
                        limit=limit,
                    )
                )
        if not prefetch:
            return []

        if len(prefetch) == 1:
            branch = prefetch[0]
            response = await self._client.query_points(
                alias_for(config.strategy),
                query=branch.query,
                using=branch.using,
                query_filter=flt,
                limit=limit,
                with_payload=True,
            )
        else:
            response = await self._client.query_points(
                alias_for(config.strategy),
                prefetch=prefetch,
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=limit,
                with_payload=True,
            )
        hits = [_hit(p) for p in response.points]

        if config.rerank_top and self._reranker is not None and hits:
            head = hits[: config.rerank_top]
            scores = await asyncio.to_thread(self._reranker.scores, query, [h.text for h in head])
            reranked = sorted(zip(scores, head, strict=True), key=lambda pair: -pair[0])
            hits = [
                Hit(h.key, h.article_key, h.parent_key, h.text, s, h.valid_from, h.cites)
                for s, h in reranked
            ] + hits[config.rerank_top :]
        return hits[:k]


def _hit(point: models.ScoredPoint | models.Record) -> Hit:
    p = point.payload or {}
    return Hit(
        key=str(p["key"]),
        article_key=str(p["article_key"]),
        parent_key=p.get("parent_key"),
        text=str(p["text"]),
        # Scrolled records carry no score; only ranked results do.
        score=float(getattr(point, "score", 0.0)),
        valid_from=date.fromordinal(int(p["valid_from"])),
        cites=tuple(p.get("cites", ())),
    )


def _natural_key(key: str) -> tuple[tuple[int, ...], ...]:
    """Order provision keys as printed: part:3 < part:3-1 < part:4 < part:10."""
    return tuple(tuple(int(n) for n in re.findall(r"\d+", segment)) for segment in key.split("/"))


def merge_article(chunks: Sequence[Hit]) -> str:
    """Join an article's chunks back into one text, keeping a single breadcrumb line."""
    ordered = sorted(chunks, key=lambda h: _natural_key(h.key))
    lines = ordered[0].text.splitlines()[:1]
    for chunk in ordered:
        lines.extend(chunk.text.splitlines()[1:])
    return "\n".join(lines)


async def expand_to_articles(
    client: AsyncQdrantClient, hits: Sequence[Hit], *, source_id: int, as_of: date
) -> list[Hit]:
    """Parent-child retrieval: search finds the best *part*; the reader gets the whole article.

    Long articles are indexed as several part chunks. Answering from one part loses the
    article's other conditions and exceptions, so each hit is replaced by its full article
    (all chunks valid on ``as_of``), in the order the hits ranked their articles.
    """
    order = list(dict.fromkeys(h.article_key for h in hits))
    if not order:
        return []
    flt = temporal_filter(
        source_id,
        as_of,
        models.FieldCondition(key="article_key", match=models.MatchAny(any=order)),
    )
    points, _ = await client.scroll(
        alias_for(Strategy.ARTICLE), scroll_filter=flt, limit=1000, with_payload=True
    )
    by_article: dict[str, list[Hit]] = {}
    for point in points:
        hit = _hit(point)
        by_article.setdefault(hit.article_key, []).append(hit)

    best: dict[str, Hit] = {}
    for h in hits:
        best.setdefault(h.article_key, h)
    expanded = []
    for article_key in order:
        chunks = by_article.get(article_key)
        if not chunks:  # not in the article index (should not happen): keep the original hit
            expanded.append(best[article_key])
            continue
        top = best[article_key]
        expanded.append(
            Hit(
                key=article_key,
                article_key=article_key,
                parent_key=article_key,
                text=merge_article(chunks),
                score=top.score,
                valid_from=max(c.valid_from for c in chunks),
                cites=tuple(dict.fromkeys(k for c in chunks for k in c.cites)),
            )
        )
    return expanded
