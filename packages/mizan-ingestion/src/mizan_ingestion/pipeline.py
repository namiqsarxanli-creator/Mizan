"""Sync one legal document from e-qanun into Postgres.

    fetch metadata -> for each version: fetch -> hash -> (skip | parse -> store) -> validity

Idempotent by construction: a version is identified by the SHA-256 of its raw HTML, so a
re-run downloads nothing it already has (historical versions are cached on disk), parses
nothing it already parsed with the current parser, and writes nothing new. Each version is
stored in its own transaction, so an interrupted run resumes where it stopped.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mizan_core.logging import get_logger
from mizan_ingestion.db import repository as repo
from mizan_ingestion.parsing import PARSER_VERSION, parse_document
from mizan_ingestion.parsing.structure import flatten
from mizan_ingestion.sources.eqanun import SOURCE, EQanunClient, RawDocument

log = get_logger(__name__)


class Outcome(StrEnum):
    INSERTED = "inserted"
    REPARSED = "reparsed"
    UNCHANGED = "unchanged"


@dataclass
class SyncReport:
    source_id: int
    title: str = ""
    outcomes: Counter[Outcome] = field(default_factory=Counter)
    downloaded: int = 0
    orphan_texts_deleted: int = 0
    warnings: dict[str, list[str]] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.outcomes[Outcome.INSERTED] or self.outcomes[Outcome.REPARSED])


async def _ingest(
    session: AsyncSession,
    document_id: int,
    raw: RawDocument,
    info: repo.VersionInfo,
    *,
    force_reparse: bool,
    report: SyncReport,
) -> tuple[int, Outcome]:
    existing = await repo.find_version(session, document_id, info.raw_sha256)
    if existing and existing.parser_version >= PARSER_VERSION and not force_reparse:
        await repo.update_version_meta(session, existing.id, info)
        return existing.id, Outcome.UNCHANGED

    parsed = parse_document(raw.content)
    provisions, flatten_warnings = flatten(parsed.root)
    warnings = [*parsed.warnings, *flatten_warnings]
    if warnings:
        report.warnings[raw.url] = warnings
        log.warning("parse_warnings", url=raw.url, warnings=warnings)

    version_id = await repo.store_version(
        session,
        document_id,
        info,
        parsed,
        provisions,
        parser_version=PARSER_VERSION,
        warnings=warnings,
        replace=existing,
    )
    log.info(
        "version_stored",
        url=raw.url,
        valid_from=str(info.valid_from),
        provisions=len(provisions),
        articles=len(parsed.articles),
        encoding=parsed.encoding,
    )
    return version_id, Outcome.REPARSED if existing else Outcome.INSERTED


def _estimate_current_valid_from(raw: RawDocument, last_version: date | None) -> date:
    """The current text's start date is not published; derive a conservative estimate.

    The newest amending act in the endnotes was adopted before the text could change, and the
    current text must start after the newest dated historical version.
    """
    parsed = parse_document(raw.content)
    acts = [e.act.adopted_on for e in parsed.endnotes.values() if e.act]
    candidates = [d for d in (max(acts, default=None), last_version) if d is not None]
    if not candidates:
        raise ValueError(f"cannot date current version of {raw.url}")
    newest = max(candidates)
    return newest + timedelta(days=1) if newest == last_version else newest


async def sync_document(
    client: EQanunClient,
    sessions: async_sessionmaker[AsyncSession],
    source_id: int,
    *,
    force_reparse: bool = False,
) -> SyncReport:
    report = SyncReport(source_id=source_id)
    meta = await client.get_framework(source_id)
    versions = await client.get_versions(source_id)
    report.title = meta.title
    log.info("sync_started", source_id=source_id, title=meta.title, versions=len(versions))

    async with sessions.begin() as session:
        document_id = await repo.upsert_document(
            session,
            repo.DocumentInfo(
                source=SOURCE,
                source_id=source_id,
                title=meta.title,
                doc_type=meta.doc_type,
                status=meta.status,
                accepted_on=meta.accepted_on,
            ),
        )

    seen_hashes: dict[str, int] = {}
    for v in versions:
        raw = await client.fetch_html(v.html_url, immutable=True)
        report.downloaded += not raw.from_cache
        info = repo.VersionInfo(
            source_version_id=v.source_version_id,
            is_current=False,
            valid_from=v.effective_on,
            valid_from_estimated=False,
            source_url=v.html_url,
            raw_sha256=raw.sha256,
        )
        if raw.sha256 in seen_hashes:
            # The source occasionally lists the same text twice; the earliest date wins.
            log.info("duplicate_version_skipped", url=v.html_url)
            continue
        async with sessions.begin() as session:
            version_id, outcome = await _ingest(
                session, document_id, raw, info, force_reparse=force_reparse, report=report
            )
        seen_hashes[raw.sha256] = version_id
        report.outcomes[outcome] += 1

    current_raw = await client.fetch_html(meta.html_url, immutable=False)
    report.downloaded += 1
    async with sessions.begin() as session:
        if current_raw.sha256 in seen_hashes:
            current_id = seen_hashes[current_raw.sha256]
        else:
            last = versions[-1].effective_on if versions else None
            info = repo.VersionInfo(
                source_version_id=None,
                is_current=True,
                valid_from=_estimate_current_valid_from(current_raw, last),
                valid_from_estimated=True,
                source_url=meta.html_url,
                raw_sha256=current_raw.sha256,
            )
            current_id, outcome = await _ingest(
                session, document_id, current_raw, info, force_reparse=force_reparse, report=report
            )
            report.outcomes[outcome] += 1
        await repo.mark_current(session, document_id, current_id)
        replaced = await repo.delete_republished_current(session, document_id)
        await repo.recompute_validity(session, document_id)
        if replaced:
            log.info("republished_current_replaced", source_id=source_id, versions=replaced)
        if report.outcomes[Outcome.REPARSED] or replaced:
            report.orphan_texts_deleted = await repo.delete_orphan_texts(session)

    log.info(
        "sync_finished",
        source_id=source_id,
        outcomes={k.value: v for k, v in report.outcomes.items()},
        downloaded=report.downloaded,
    )
    return report
