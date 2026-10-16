"""Build the retrieval corpus: chunks with validity intervals.

Chunks are produced per stored version, then merged: a chunk whose text is identical in
consecutive versions becomes *one* record valid over the union of their intervals. The Labour
Code's 14 versions yield ~380 article chunks each, but only ~1,100 distinct (chunk, interval)
records, so each distinct text is embedded once and an ``as_of`` filter selects the right one.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mizan_ingestion import models
from mizan_ingestion.chunking import Chunk, Strategy, article_chunks, part_chunks
from mizan_ingestion.db import repository as repo
from mizan_ingestion.db.schema import DocumentVersion, LegalDocument
from mizan_ingestion.db.schema import Provision as ProvisionRow
from mizan_ingestion.sources.eqanun import SOURCE

_NAMESPACE = uuid.UUID("6f1c2d1e-5a0b-4c7e-9a43-0d6c6b1f2a10")
OPEN_END = date.max  # valid_to of the newest version


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    source_id: int
    strategy: Strategy
    key: str
    parent_key: str | None
    cites: tuple[str, ...]
    text: str
    valid_from: date
    valid_to: date  # exclusive; OPEN_END for "still in force"

    @property
    def article_key(self) -> str:
        return self.key.split("/", 1)[0]

    @property
    def point_id(self) -> str:
        digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        name = f"{self.source_id}|{self.strategy}|{self.key}|{digest}|{self.valid_from}"
        return str(uuid.uuid5(_NAMESPACE, name))


def _to_domain(rows: list[ProvisionRow]) -> list[models.Provision]:
    return [
        models.Provision(
            key=r.key,
            parent_key=r.parent_key,
            kind=models.NodeKind(r.kind),
            number=r.number,
            heading=r.content.heading,
            text=r.content.text,
            ordinal=r.ordinal,
            depth=r.depth,
            endnote_refs=tuple(r.endnote_refs),
            linked_acts=tuple(r.linked_acts),
        )
        for r in rows
    ]


def _chunk(
    provisions: list[models.Provision], strategy: Strategy, title: str, article_max_chars: int
) -> list[Chunk]:
    if strategy is Strategy.ARTICLE:
        return article_chunks(provisions, document_title=title, max_chars=article_max_chars)
    if strategy is Strategy.PART:
        return part_chunks(provisions, document_title=title)
    raise ValueError(f"strategy {strategy} has no provision keys and cannot be cited")


async def load_chunk_records(
    sessions: async_sessionmaker[AsyncSession],
    source_id: int,
    strategy: Strategy,
    *,
    article_max_chars: int = 1800,
) -> list[ChunkRecord]:
    async with sessions() as session:
        document = (
            await session.execute(
                select(LegalDocument).where(
                    LegalDocument.source == SOURCE, LegalDocument.source_id == source_id
                )
            )
        ).scalar_one()
        versions = (
            (
                await session.execute(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == document.id)
                    .order_by(DocumentVersion.valid_from)
                )
            )
            .scalars()
            .all()
        )
        open_: dict[tuple[str, str], tuple[date, Chunk]] = {}
        records: list[ChunkRecord] = []

        def close(identity: tuple[str, str], end: date) -> None:
            start, chunk = open_.pop(identity)
            records.append(
                ChunkRecord(
                    source_id=source_id,
                    strategy=strategy,
                    key=chunk.key,
                    parent_key=chunk.parent_key,
                    cites=chunk.cites,
                    text=chunk.text,
                    valid_from=start,
                    valid_to=end,
                )
            )

        for version in versions:
            _, rows = await repo.provisions_as_of(
                session, source=SOURCE, source_id=source_id, on=version.valid_from
            )
            chunks = _chunk(_to_domain(rows), strategy, document.title, article_max_chars)
            current = {(c.key, c.text): c for c in chunks}
            for identity in [i for i in open_ if i not in current]:
                close(identity, version.valid_from)
            for identity, chunk in current.items():
                open_.setdefault(identity, (version.valid_from, chunk))
        for identity in list(open_):
            close(identity, OPEN_END)
    return records
