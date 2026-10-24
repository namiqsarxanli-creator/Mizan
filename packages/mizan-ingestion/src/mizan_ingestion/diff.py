"""How a provision changed between two dates, and which acts changed it.

    art:69 on 2020-01-01 ──┐
                           ├─ provision-by-provision comparison ─ word-level diff
    art:69 on 2026-01-01 ──┘                                       + amending acts (endnotes)

Both sides come from the temporal store, so "from" and "to" are dates, not version ids: the
caller asks what a lawyer asks ("what changed in Art. 69 since 2020?").
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mizan_ingestion.db import repository as repo
from mizan_ingestion.db.schema import Endnote as EndnoteRow
from mizan_ingestion.db.schema import Provision as ProvisionRow
from mizan_ingestion.parsing.endnotes import split_amendments
from mizan_ingestion.sources.eqanun import SOURCE

_TOKEN = re.compile(r"\s+|[^\s]+")


class ChangeKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class Text:
    """What a provision says: its heading and its own text (children are separate keys)."""

    key: str
    heading: str | None
    text: str

    @property
    def full(self) -> str:
        return "\n".join(part for part in (self.heading, self.text) if part)


@dataclass(frozen=True, slots=True)
class ProvisionChange:
    key: str
    kind: ChangeKind
    before: str | None
    after: str | None
    marked: str  # word-level diff: "[-removed-]{+added+}"


@dataclass(frozen=True, slots=True)
class Amendment:
    number: str | None  # "332-VIIQD"
    adopted_on: date
    note: str  # the endnote as printed, e.g. "... ilə 69-cu maddənin adı yeni redaksiyada ..."


@dataclass(frozen=True, slots=True)
class ArticleDiff:
    key: str
    start: date
    end: date
    start_version_from: date
    end_version_from: date
    changes: list[ProvisionChange]
    amendments: list[Amendment]

    @property
    def unchanged(self) -> bool:
        return not self.changes


def mark_words(before: str, after: str) -> str:
    """Inline word diff, readable without colours: ``[-old-]{+new+}``."""
    a, b = _TOKEN.findall(before), _TOKEN.findall(after)
    out: list[str] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.extend(a[i1:i2])
            continue
        if op in ("delete", "replace"):
            out.append(f"[-{''.join(a[i1:i2])}-]")
        if op in ("insert", "replace"):
            out.append(f"{{+{''.join(b[j1:j2])}+}}")
    return "".join(out)


def compare(before: Sequence[Text], after: Sequence[Text]) -> list[ProvisionChange]:
    """Provision-level changes, in the order the provisions are printed in ``after``."""
    old = {t.key: t for t in before}
    new = {t.key: t for t in after}
    changes: list[ProvisionChange] = []
    for t in after:
        previous = old.get(t.key)
        if previous is None:
            changes.append(
                ProvisionChange(t.key, ChangeKind.ADDED, None, t.full, f"{{+{t.full}+}}")
            )
        elif previous.full != t.full:
            changes.append(
                ProvisionChange(
                    t.key,
                    ChangeKind.CHANGED,
                    previous.full,
                    t.full,
                    mark_words(previous.full, t.full),
                )
            )
    changes += [
        ProvisionChange(t.key, ChangeKind.REMOVED, t.full, None, f"[-{t.full}-]")
        for t in before
        if t.key not in new
    ]
    return changes


class DiffBackend(Protocol):
    async def diff(self, source_id: int, key: str, start: date, end: date) -> ArticleDiff | None:
        """``None`` when no version of the document is in force on one of the dates."""
        ...


class PostgresDiff:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def diff(self, source_id: int, key: str, start: date, end: date) -> ArticleDiff | None:
        async with self._sessions() as session:
            v_start, p_start = await repo.provisions_as_of(
                session, source=SOURCE, source_id=source_id, on=start, key_prefix=key
            )
            v_end, p_end = await repo.provisions_as_of(
                session, source=SOURCE, source_id=source_id, on=end, key_prefix=key
            )
            if v_start is None or v_end is None:
                return None
            refs = sorted({r for p in p_end for r in p.endnote_refs})
            notes = (
                (
                    await session.execute(
                        select(EndnoteRow).where(
                            EndnoteRow.version_id == v_end.id, EndnoteRow.number.in_(refs)
                        )
                    )
                )
                .scalars()
                .all()
            )

        def texts(rows: Sequence[ProvisionRow]) -> list[Text]:
            return [Text(r.key, r.content.heading, r.content.text) for r in rows]

        amendments: dict[tuple[str | None, date, str], Amendment] = {}
        for note in sorted(notes, key=lambda n: n.number):
            for act, sentence in split_amendments(note.text):
                # Endnotes accumulate over the years: keep the acts adopted inside the window.
                if start < act.adopted_on <= end:
                    amendment = Amendment(act.number, act.adopted_on, sentence)
                    amendments.setdefault((act.number, act.adopted_on, sentence), amendment)
        return ArticleDiff(
            key=key,
            start=start,
            end=end,
            start_version_from=v_start.valid_from,
            end_version_from=v_end.valid_from,
            changes=compare(texts(p_start), texts(p_end)),
            amendments=sorted(amendments.values(), key=lambda a: a.adopted_on),
        )
