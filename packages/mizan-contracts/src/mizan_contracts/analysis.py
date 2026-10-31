"""The contract analysis pipeline: document -> clauses -> findings -> report.

1. Read the document (``document.py``); scanned pages go through OCR.
2. Split it into clauses (``clauses.py``).
3. Screen every clause for injected instructions; flagged clauses are reported and skipped.
4. Run the deterministic rules (``rules.py``).
5. Review the remaining clauses with the LLM (``review.py``), when one is configured. Clauses
   a rule already flagged are not sent: the rule's verdict stands, and the tokens are saved.
6. One verdict per clause, the most severe of its findings: injection > violation > risk > ok.

The report always carries the disclaimer. It is a research aid that points at provisions, not
legal advice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Protocol

from mizan_contracts.clauses import Clause, split_clauses
from mizan_contracts.document import Document
from mizan_contracts.review import ReviewStats
from mizan_contracts.rules import Context, Finding, check, context_of

ClauseVerdict = Literal["ok", "risk", "violation", "injection"]
SEVERITY: dict[ClauseVerdict, int] = {"ok": 0, "risk": 1, "violation": 2, "injection": 3}

DISCLAIMER = (
    "Bu hesabat avtomatik hazırlanmış tədqiqat vasitəsidir, hüquqi məsləhət deyil. Hər nəticəni "
    "göstərilən maddənin mətni ilə yoxlayın; qərar verməzdən əvvəl hüquqşünasa müraciət edin. "
    "«Uyğun» işarəsi bəndin qanuni olduğuna zəmanət deyil: yalnız yoxlanılan maddələrlə "
    "ziddiyyət tapılmadığını göstərir."
)


class Reviewer(Protocol):
    async def review(
        self, clauses: Sequence[Clause], *, as_of: date, ctx: Context
    ) -> tuple[list[Finding], ReviewStats]: ...


@dataclass(frozen=True, slots=True)
class ClauseResult:
    number: str
    section: str | None
    text: str
    verdict: ClauseVerdict
    findings: tuple[Finding, ...]


@dataclass(frozen=True, slots=True)
class Report:
    filename: str
    as_of: date
    clauses: tuple[ClauseResult, ...]
    context: Context
    ocr_pages: tuple[int, ...] = ()
    unreadable_pages: tuple[int, ...] = ()
    llm_reviewed: bool = False
    review: ReviewStats = field(default_factory=ReviewStats)
    disclaimer: str = DISCLAIMER

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = dict.fromkeys(SEVERITY, 0)
        for c in self.clauses:
            out[c.verdict] += 1
        return out


def verdict_of(findings: Sequence[Finding]) -> ClauseVerdict:
    worst: ClauseVerdict = "ok"
    for f in findings:
        if SEVERITY[f.verdict] > SEVERITY[worst]:
            worst = f.verdict
    return worst


async def analyse(document: Document, *, as_of: date, reviewer: Reviewer | None = None) -> Report:
    clauses = split_clauses(document.text)
    ctx = context_of(clauses)
    findings = check(clauses, ctx)
    flagged = {f.clause for f in findings}
    stats = ReviewStats()
    if reviewer is not None:
        pending = [c for c in clauses if c.number not in flagged]
        llm_findings, stats = await reviewer.review(pending, as_of=as_of, ctx=ctx)
        findings += llm_findings
    by_clause: dict[str, list[Finding]] = {}
    for f in findings:
        by_clause.setdefault(f.clause, []).append(f)
    results = tuple(
        ClauseResult(
            number=c.number,
            section=c.section,
            text=c.text,
            verdict=verdict_of(by_clause.get(c.number, [])),
            findings=tuple(by_clause.get(c.number, [])),
        )
        for c in clauses
    )
    return Report(
        filename=document.filename,
        as_of=as_of,
        clauses=results,
        context=ctx,
        ocr_pages=tuple(document.ocr_pages),
        unreadable_pages=tuple(document.unreadable_pages),
        llm_reviewed=reviewer is not None,
        review=stats,
    )
