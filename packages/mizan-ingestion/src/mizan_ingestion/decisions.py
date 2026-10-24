"""Acts linked from a code, and Constitutional Court decisions in particular.

The Word export links provisions to other acts on e-qanun (``provisions.linked_acts``): laws it
relies on, and the Constitutional Court decisions interpreting it (the "KMQ" markers). This
module registers those acts in ``legal_documents`` (metadata only) and stores the text of each
Constitutional Court decision, split into its reasoning ("MÜƏYYƏN ETDİ:") and its operative
part ("QƏRARA ALDI:"), with the outcome classified from the operative text.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mizan_core.logging import get_logger
from mizan_ingestion.db import repository as repo
from mizan_ingestion.db.schema import CourtDecision, DocumentVersion, LegalDocument, Provision
from mizan_ingestion.parsing.encoding import decode_html
from mizan_ingestion.parsing.word_html import parse_word_html
from mizan_ingestion.sources.eqanun import SOURCE, EQanunClient, SourceError
from mizan_ingestion.text import az_lower

log = get_logger(__name__)

DECISION_PARSER_VERSION = 1
CONSTITUTIONAL_COURT_TYPE = "konstitusiya məhkəməsinin qərarları"

_REASONING = re.compile(r"^müəyyən\s+etdi\s*:?$")
_OPERATIVE = re.compile(r"^qərara\s+aldı\s*:?$")
_SIGNATURE = re.compile(r"^(sədr|bakı şəhəri)\b")
# Outcome markers in the operative part, lower-cased.
_UNCONSTITUTIONAL = re.compile(
    r"uyğun\s+(?:olmadığına|olmadığından|gəlmədiyinə)|uyğunsuzluq\s+təşkil\s+etdiyinə"
    r"|etibarsız\s+hesab\s+edilsin|qüvvədən\s+düşmüş\s+hesab\s+edilsin|tətbiqi\s+dayandırılsın"
)
_CONSTITUTIONAL = re.compile(r"uyğun\s+(?:hesab\s+edilsin|hesab\s+edilir)")
_INTERPRETATION = re.compile(r"şərh\s+edilməsinə\s+dair")


class Outcome(StrEnum):
    UNCONSTITUTIONAL = "unconstitutional"  # (part of) a provision is void
    CONSTITUTIONAL = "constitutional"  # checked and upheld
    INTERPRETATION = "interpretation"  # how a provision must be read
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ParsedDecision:
    title: str
    reasoning: str
    operative: str
    outcome: Outcome


def classify(title: str, operative: str) -> Outcome:
    text = az_lower(operative)
    if _UNCONSTITUTIONAL.search(text):
        return Outcome.UNCONSTITUTIONAL
    if _CONSTITUTIONAL.search(text):
        return Outcome.CONSTITUTIONAL
    if _INTERPRETATION.search(az_lower(title)):
        return Outcome.INTERPRETATION
    return Outcome.OTHER


def parse_decision(raw: bytes, *, title: str) -> ParsedDecision:
    """Split a decision into reasoning and operative part.

    The operative heading "QƏRARA ALDI:" can also be quoted inside the reasoning; the last one
    opens the operative part, which ends at the signature ("Sədr: ...", "Bakı şəhəri, ...").
    """
    paragraphs = [p.text for p in parse_word_html(decode_html(raw).text).paragraphs]
    lowered = [az_lower(p) for p in paragraphs]
    reasoning_at = next((i for i, p in enumerate(lowered) if _REASONING.match(p)), None)
    operative_at = max((i for i, p in enumerate(lowered) if _OPERATIVE.match(p)), default=None)
    if operative_at is None:
        raise SourceError(f"decision {title!r} has no operative part")
    end = next(
        (i for i in range(operative_at + 1, len(lowered)) if _SIGNATURE.match(lowered[i])),
        len(lowered),
    )
    start = reasoning_at + 1 if reasoning_at is not None and reasoning_at < operative_at else 0
    reasoning = "\n".join(paragraphs[start:operative_at])
    operative = "\n".join(paragraphs[operative_at + 1 : end])
    return ParsedDecision(title, reasoning, operative, classify(title, operative))


@dataclass(slots=True)
class LinkedActsReport:
    acts: int = 0
    by_type: Counter[str] = field(default_factory=Counter)
    decisions_stored: int = 0
    failed: list[int] = field(default_factory=list)


async def _linked_ids(sessions: async_sessionmaker[AsyncSession], source_id: int) -> list[int]:
    async with sessions() as session:
        rows = await session.execute(
            select(Provision.linked_acts)
            .join(DocumentVersion, Provision.version_id == DocumentVersion.id)
            .join(LegalDocument, DocumentVersion.document_id == LegalDocument.id)
            .where(LegalDocument.source == SOURCE, LegalDocument.source_id == source_id)
        )
        return sorted({act for (acts,) in rows for act in acts if act != source_id})


async def sync_linked_acts(
    client: EQanunClient, sessions: async_sessionmaker[AsyncSession], source_id: int
) -> LinkedActsReport:
    """Register every act linked from ``source_id``; store Constitutional Court decisions."""
    report = LinkedActsReport()
    for act_id in await _linked_ids(sessions, source_id):
        try:
            meta = await client.get_framework(act_id)
        except SourceError:
            log.warning("linked_act_unavailable", act=act_id, exc_info=True)
            report.failed.append(act_id)
            continue
        async with sessions.begin() as session:
            document_id = await repo.upsert_document(
                session,
                repo.DocumentInfo(
                    source=SOURCE,
                    source_id=act_id,
                    title=meta.title,
                    doc_type=meta.doc_type,
                    status=meta.status,
                    accepted_on=meta.accepted_on,
                ),
            )
        report.acts += 1
        report.by_type[meta.doc_type or "?"] += 1
        if az_lower(meta.doc_type or "") != CONSTITUTIONAL_COURT_TYPE or meta.accepted_on is None:
            continue
        # A decision is final: its text never changes, so the download is cached for good.
        raw = await client.fetch_html(meta.html_url, immutable=True)
        decision = parse_decision(raw.content, title=meta.title)
        async with sessions.begin() as session:
            await _store_decision(session, document_id, meta.accepted_on, raw.sha256, decision)
        report.decisions_stored += 1
    log.info(
        "linked_acts_synced",
        source_id=source_id,
        acts=report.acts,
        decisions=report.decisions_stored,
        failed=report.failed,
    )
    return report


async def _store_decision(
    session: AsyncSession, document_id: int, adopted_on: date, sha256: str, d: ParsedDecision
) -> None:
    values = {
        "document_id": document_id,
        "court": "constitutional",
        "adopted_on": adopted_on,
        "outcome": d.outcome.value,
        "reasoning": d.reasoning,
        "operative": d.operative,
        "raw_sha256": sha256,
        "parser_version": DECISION_PARSER_VERSION,
    }
    stmt = insert(CourtDecision).values(**values)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[CourtDecision.document_id],
            set_={k: stmt.excluded[k] for k in values if k != "document_id"},
        )
    )
