"""Persistence operations. All functions take a session; the caller owns the transaction."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import batched
from typing import Any

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mizan_ingestion import models
from mizan_ingestion.db.schema import (
    DocumentVersion,
    Endnote,
    LegalDocument,
    Provision,
    ProvisionText,
)

_BATCH = 1000  # keeps every INSERT well under Postgres' 32767 bind-parameter limit


@dataclass(frozen=True, slots=True)
class DocumentInfo:
    source: str
    source_id: int
    title: str
    doc_type: str | None
    status: str | None
    accepted_on: date | None


@dataclass(frozen=True, slots=True)
class VersionInfo:
    source_version_id: int | None
    is_current: bool
    valid_from: date
    valid_from_estimated: bool
    source_url: str
    raw_sha256: str


async def upsert_document(session: AsyncSession, info: DocumentInfo) -> int:
    values = {
        "source": info.source,
        "source_id": info.source_id,
        "title": info.title,
        "doc_type": info.doc_type,
        "status": info.status,
        "accepted_on": info.accepted_on,
    }
    stmt = (
        insert(LegalDocument)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["source", "source_id"],
            set_={k: v for k, v in values.items() if k not in {"source", "source_id"}},
        )
        .returning(LegalDocument.id)
    )
    return (await session.execute(stmt)).scalar_one()


async def find_version(
    session: AsyncSession, document_id: int, sha256: str
) -> DocumentVersion | None:
    stmt = select(DocumentVersion).where(
        DocumentVersion.document_id == document_id, DocumentVersion.raw_sha256 == sha256
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _insert_batched(session: AsyncSession, stmt: Any, rows: Sequence[dict[str, Any]]) -> None:
    for batch in batched(rows, _BATCH):
        await session.execute(stmt, list(batch))


async def store_version(
    session: AsyncSession,
    document_id: int,
    info: VersionInfo,
    parsed: models.ParsedDocument,
    provisions: Sequence[models.Provision],
    *,
    parser_version: int,
    warnings: Iterable[str],
    replace: DocumentVersion | None = None,
) -> int:
    """Insert (or replace the parsed content of) one version."""
    meta = {
        "source_version_id": info.source_version_id,
        "is_current": info.is_current,
        "valid_from": info.valid_from,
        "valid_from_estimated": info.valid_from_estimated,
        "source_url": info.source_url,
        "encoding": parsed.encoding,
        "parser_version": parser_version,
        "parse_warnings": list(warnings),
    }
    if replace is not None:
        version_id = replace.id
        await session.execute(delete(Provision).where(Provision.version_id == version_id))
        await session.execute(delete(Endnote).where(Endnote.version_id == version_id))
        await session.execute(
            update(DocumentVersion).where(DocumentVersion.id == version_id).values(**meta)
        )
    else:
        stmt = (
            insert(DocumentVersion)
            .values(document_id=document_id, raw_sha256=info.raw_sha256, **meta)
            .returning(DocumentVersion.id)
        )
        version_id = (await session.execute(stmt)).scalar_one()

    texts = {
        p.content_hash: {"content_hash": p.content_hash, "heading": p.heading, "text": p.text}
        for p in provisions
    }
    await _insert_batched(
        session, insert(ProvisionText).on_conflict_do_nothing(), list(texts.values())
    )
    await _insert_batched(
        session,
        insert(Provision),
        [
            {
                "version_id": version_id,
                "key": p.key,
                "parent_key": p.parent_key,
                "kind": p.kind.value,
                "number": p.number,
                "ordinal": p.ordinal,
                "depth": p.depth,
                "content_hash": p.content_hash,
                "endnote_refs": list(p.endnote_refs),
            }
            for p in provisions
        ],
    )
    await _insert_batched(
        session,
        insert(Endnote),
        [
            {
                "version_id": version_id,
                "number": e.number,
                "text": e.text,
                "act_number": e.act.number if e.act else None,
                "act_adopted_on": e.act.adopted_on if e.act else None,
            }
            for e in parsed.endnotes.values()
        ],
    )
    return version_id


async def update_version_meta(session: AsyncSession, version_id: int, info: VersionInfo) -> None:
    await session.execute(
        update(DocumentVersion)
        .where(DocumentVersion.id == version_id)
        .values(
            source_version_id=info.source_version_id,
            valid_from=info.valid_from,
            valid_from_estimated=info.valid_from_estimated,
            source_url=info.source_url,
        )
    )


async def mark_current(session: AsyncSession, document_id: int, version_id: int) -> None:
    await session.execute(
        update(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .values(is_current=DocumentVersion.id == version_id)
    )


async def delete_orphan_texts(session: AsyncSession) -> int:
    """Drop texts no provision points to any more (left behind by re-parsing)."""
    referenced = select(Provision.content_hash).where(
        Provision.content_hash == ProvisionText.content_hash
    )
    result = await session.execute(delete(ProvisionText).where(~referenced.exists()))
    return int(getattr(result, "rowcount", 0) or 0)


async def recompute_validity(session: AsyncSession, document_id: int) -> None:
    """valid_to of each version = valid_from of the next one; the newest stays open."""
    rows = (
        await session.execute(
            select(DocumentVersion.id, DocumentVersion.valid_from)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.valid_from, DocumentVersion.id)
        )
    ).all()
    for (version_id, _), nxt in zip(rows, [*rows[1:], None], strict=True):
        await session.execute(
            update(DocumentVersion)
            .where(DocumentVersion.id == version_id)
            .values(valid_to=nxt.valid_from if nxt else None)
        )


async def provisions_as_of(
    session: AsyncSession,
    *,
    source: str,
    source_id: int,
    on: date,
    key_prefix: str | None = None,
) -> tuple[DocumentVersion | None, list[Provision]]:
    """The structure and text of a document as it stood on ``on``."""
    version = (
        await session.execute(
            select(DocumentVersion)
            .join(LegalDocument)
            .where(
                LegalDocument.source == source,
                LegalDocument.source_id == source_id,
                DocumentVersion.valid_from <= on,
                or_(DocumentVersion.valid_to.is_(None), DocumentVersion.valid_to > on),
            )
        )
    ).scalar_one_or_none()
    if version is None:
        return None, []
    conditions = [Provision.version_id == version.id]
    if key_prefix:
        conditions.append(
            or_(Provision.key == key_prefix, Provision.key.startswith(f"{key_prefix}/"))
        )
    provisions = (
        (
            await session.execute(
                select(Provision).where(and_(*conditions)).order_by(Provision.ordinal)
            )
        )
        .scalars()
        .all()
    )
    return version, list(provisions)
