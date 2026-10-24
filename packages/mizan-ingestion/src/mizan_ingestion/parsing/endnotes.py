"""Parse amendment endnotes.

Typical endnote::

    30 dekabr 2025-ci il tarixli 332-VIIQD nömrəli Azərbaycan Respublikasının Qanunu (...)
    ilə 69-cu maddənin adı yeni redaksiyada verilmişdir.
"""

from __future__ import annotations

import re
from datetime import date

from mizan_ingestion.models import AmendingAct, Endnote

MONTHS = {
    "yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4, "may": 5, "iyun": 6,
    "iyul": 7, "avqust": 8, "sentyabr": 9, "oktyabr": 10, "noyabr": 11, "dekabr": 12,
}  # fmt: skip

# The format drifts across 25 years of endnotes: "tarixli" and "nömrəli" are sometimes
# missing, a comma may follow "tarixli", and a few endnotes omit the act number entirely.
_ACT = re.compile(
    r"(?P<day>\d{1,2})\s+(?P<month>[a-zəğıöüşç]+)\s+(?P<year>\d{4})\s*-?\s*c[iıuü]\s+il"
    r"(?:\s+tarixli)?,?\s*(?P<number>\d+-[IVXLC]+[A-ZƏ]*)?",
    re.IGNORECASE,
)


def parse_amending_act(text: str) -> AmendingAct | None:
    match = _ACT.search(text)
    if not match:
        return None
    month = MONTHS.get(match.group("month").lower())
    if month is None:
        return None
    try:
        adopted = date(int(match.group("year")), month, int(match.group("day")))
    except ValueError:
        return None
    return AmendingAct(number=match.group("number"), adopted_on=adopted)


def split_amendments(text: str) -> list[tuple[AmendingAct, str]]:
    """One endnote can record several amendments, each starting with its act's date.

    "28 iyun 2024-cü il tarixli 1195-VIQD ... əlavə edilmişdir. 30 dekabr 2025-ci il tarixli
    332-VIIQD ... əvəz edilmişdir." -> two (act, sentence) pairs.
    """
    starts = [m for m in _ACT.finditer(text) if _is_act_start(text, m.start())]
    out = []
    for m, nxt in zip(starts, [*starts[1:], None], strict=True):
        segment = text[m.start() : nxt.start() if nxt else len(text)].strip()
        if act := parse_amending_act(segment):
            out.append((act, segment))
    return out


def _is_act_start(text: str, at: int) -> bool:
    """A date opens a new amendment at the start or after a sentence; other dates are
    publication details ("rəsmi internet saytı, 12 avqust 2024-cü il")."""
    before = text[:at].rstrip()
    return not before or before.endswith((".", ";"))


def parse_endnotes(raw: dict[int, str]) -> dict[int, Endnote]:
    return {n: Endnote(n, text, parse_amending_act(text)) for n, text in sorted(raw.items())}
