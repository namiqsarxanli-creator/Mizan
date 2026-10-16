"""Qdrant collections: blue-green rebuilds behind an alias (ADR-0002).

A rebuild writes a new, timestamped collection, then atomically points the alias
(``legal_article``) at it and drops the old one. Searches never see a half-built index.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from qdrant_client import AsyncQdrantClient, models

from mizan_core.logging import get_logger
from mizan_ingestion.chunking import Strategy
from mizan_retrieval.analysis import ANALYZERS
from mizan_retrieval.corpus import ChunkRecord
from mizan_retrieval.embeddings import Embedder
from mizan_retrieval.sparse import BM25Encoder

log = get_logger(__name__)

_UPSERT_BATCH = 64


def alias_for(strategy: Strategy) -> str:
    return f"legal_{strategy.value}"


def payload(record: ChunkRecord) -> dict[str, object]:
    return {
        "source_id": record.source_id,
        "key": record.key,
        "article_key": record.article_key,
        "parent_key": record.parent_key,
        "cites": list(record.cites),
        "text": record.text,
        # Day ordinals: integer range filters are the cheapest temporal filter in Qdrant.
        "valid_from": record.valid_from.toordinal(),
        "valid_to": record.valid_to.toordinal(),
        "valid_from_date": record.valid_from.isoformat(),
    }


async def build_index(
    client: AsyncQdrantClient,
    strategy: Strategy,
    records: Sequence[ChunkRecord],
    embedders: Sequence[Embedder],
) -> str:
    alias = alias_for(strategy)
    # Timestamp for humans, random suffix for uniqueness: Windows clocks tick in ~1-15 ms steps,
    # so two quick rebuilds can share a timestamp.
    name = f"{alias}__{datetime.now(UTC):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}"
    texts = [r.text for r in records]

    encoders = {n: BM25Encoder.fit(a, texts) for n, a in ANALYZERS.items()}
    await client.create_collection(
        name,
        vectors_config={
            e.spec.name: models.VectorParams(size=e.spec.dim, distance=models.Distance.COSINE)
            for e in embedders
        },
        sparse_vectors_config={
            n: models.SparseVectorParams(modifier=models.Modifier.IDF) for n in encoders
        },
    )
    for field, schema in (
        ("valid_from", models.PayloadSchemaType.INTEGER),
        ("valid_to", models.PayloadSchemaType.INTEGER),
        ("source_id", models.PayloadSchemaType.INTEGER),
        ("article_key", models.PayloadSchemaType.KEYWORD),
    ):
        await client.create_payload_index(name, field, field_schema=schema)

    dense = {}
    for embedder in embedders:
        log.info("embedding", model=embedder.spec.name, texts=len(texts), strategy=strategy.value)
        dense[embedder.spec.name] = embedder.embed_passages(texts)

    for start in range(0, len(records), _UPSERT_BATCH):
        batch = records[start : start + _UPSERT_BATCH]
        points = []
        for offset, record in enumerate(batch):
            vectors: dict[str, models.Vector] = {
                model: matrix[start + offset].tolist() for model, matrix in dense.items()
            }
            for sparse_name, encoder in encoders.items():
                sparse = encoder.encode_document(record.text)
                vectors[sparse_name] = models.SparseVector(
                    indices=sparse.indices, values=sparse.values
                )
            points.append(
                models.PointStruct(id=record.point_id, vector=vectors, payload=payload(record))
            )
        await client.upsert(name, points=points, wait=True)

    await _swap_alias(client, alias, name)
    log.info("index_built", collection=name, alias=alias, points=len(records))
    return name


async def _swap_alias(client: AsyncQdrantClient, alias: str, new_collection: str) -> None:
    old = [a.collection_name for a in (await client.get_aliases()).aliases if a.alias_name == alias]
    operations: list[models.AliasOperations] = [
        models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=alias))
        for _ in old[:1]
    ]
    operations.append(
        models.CreateAliasOperation(
            create_alias=models.CreateAlias(collection_name=new_collection, alias_name=alias)
        )
    )
    await client.update_collection_aliases(change_aliases_operations=operations)
    for collection in old:
        if collection != new_collection:
            await client.delete_collection(collection)


def bm25_encoders_for_query() -> dict[str, BM25Encoder]:
    """Query-side encoders. ``avgdl`` only affects document weights, so any value works here."""
    return {n: BM25Encoder(a, avgdl=1.0) for n, a in ANALYZERS.items()}
