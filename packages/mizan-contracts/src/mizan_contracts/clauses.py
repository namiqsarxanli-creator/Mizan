"""Split contract text into numbered clauses.

Azerbaijani contracts number their clauses ("4.2.", "7.2)", "4.2 ") and group them under
numbered section headings ("4. Əmək haqqı"). A line starting with a number opens a clause; the
lines that follow (bullets, wrapped text) belong to it. A single number followed by a short
title without a full stop is a section heading, not a clause. "Maddə 3." / "Bənd 3." are read
as numbers too.

A contract without any numbering falls back to paragraphs, numbered ``¶1``, ``¶2`` ... so a
report can still point at them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUMBER = re.compile(
    r"^\s*(?:(?:maddə|bənd|madde|bend)\s+)?(\d{1,3}(?:\.\d{1,3}){0,3})\s*[.)]?(?:\s+|$)(.*)$",
    re.IGNORECASE,
)
MAX_CLAUSE_CHARS = 2000


@dataclass(frozen=True, slots=True)
class Clause:
    number: str
    section: str | None
    text: str


def _is_heading(number: str, rest: str) -> bool:
    return "." not in number and 0 < len(rest) <= 80 and not rest.rstrip().endswith((".", ";", ":"))


def split_clauses(text: str) -> list[Clause]:
    clauses: list[Clause] = []
    section: str | None = None
    number: str | None = None
    body: list[str] = []

    def flush() -> None:
        if number is not None and body:
            joined = " ".join(body).strip()
            if joined:
                clauses.append(Clause(number, section, joined[:MAX_CLAUSE_CHARS]))

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _NUMBER.match(line)
        # "2026-cı il" or "1500 manat" at the start of a wrapped line is not a clause number.
        if m and len(m.group(1).split(".")[0]) <= 2:
            n, rest = m.group(1), m.group(2).strip()
            if _is_heading(n, rest):
                flush()
                number, body = None, []
                section = f"{n}. {rest}"
                continue
            flush()
            number, body = n, [rest] if rest else []
            continue
        if number is not None:
            body.append(line)
    flush()
    if clauses:
        return _dedupe(clauses)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    return [
        Clause(f"¶{i}", None, " ".join(p.split())[:MAX_CLAUSE_CHARS])
        for i, p in enumerate(paragraphs, start=1)
    ]


def _dedupe(clauses: list[Clause]) -> list[Clause]:
    """Two clauses with one number (a template restarting its numbering) get suffixes."""
    seen: dict[str, int] = {}
    out: list[Clause] = []
    for c in clauses:
        count = seen.get(c.number, 0)
        seen[c.number] = count + 1
        out.append(c if count == 0 else Clause(f"{c.number}#{count + 1}", c.section, c.text))
    return out
