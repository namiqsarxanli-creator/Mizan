"""Constitutional Court decisions in the graph, and as context for answers.

A decision's title names the provisions it rules on ("Əmək Məcəlləsinin 144-cü maddəsinin 2 və
3-cü hissələrinin ... uyğunluğunun yoxlanılmasına dair"), so the citation grammar that links
articles also links decisions to articles: ``(:CourtDecision)-[:INTERPRETS]->(:Article)``.

Decisions matter for answers because the official consolidated text can lag behind them. On
2026-02-18 the Court suspended part of Art. 144(3) (no compensation for unused additional
leave); in October 2026 the text still says the opposite. An answer built from the text alone
is wrong, so the decisions interpreting the retrieved articles go into the context.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mizan_core.logging import get_logger
from mizan_graph.citations import extract
from mizan_ingestion.db.schema import CourtDecision, LegalDocument
from mizan_ingestion.sources.eqanun import SOURCE
from mizan_ingestion.text import az_lower
from mizan_retrieval.search import Hit

if TYPE_CHECKING:
    from mizan_graph.store import DecisionStore

log = get_logger(__name__)
DECISION_KEY_PREFIX = "kmq:"
_MONTHS = (
    "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avqust", "sentyabr", "oktyabr", "noyabr", "dekabr",
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Decision:
    eqanun_id: int
    title: str
    adopted_on: date
    outcome: str  # "interpretation" | "unconstitutional" | "constitutional" | "other"
    operative: str
    interprets: tuple[str, ...]  # article keys of the interpreted act, "art:144"

    @property
    def key(self) -> str:
        return f"{DECISION_KEY_PREFIX}{self.eqanun_id}"


def act_name(title: str, doc_type: str | None) -> str | None:
    """The name citations use for an act: "əmək məcəllə", "konstitusiya", "qanun: ..."."""
    kind = az_lower(doc_type or "")
    name = az_lower(title).removeprefix("azərbaycan respublikasının ").strip()
    if kind == "konstitusiya":
        return "konstitusiya"
    if kind == "məcəllələr" and name.endswith("məcəlləsi"):
        return name.removesuffix("si")  # "mülki prosessual məcəlləsi" -> "... məcəllə"
    if kind == "qanunlar":
        return f"qanun: {name}"
    return None


def interpreted_articles(title: str, act_names: frozenset[str]) -> tuple[str, ...]:
    """Articles of the act known as one of ``act_names`` that a decision's title rules on."""
    keys = [f"art:{c.article}" for c in extract(title, self_names=act_names) if c.internal]
    return tuple(dict.fromkeys(keys))


async def load_decisions(
    sessions: async_sessionmaker[AsyncSession], act_names: frozenset[str]
) -> list[Decision]:
    async with sessions() as session:
        rows = await session.execute(
            select(LegalDocument, CourtDecision)
            .join(CourtDecision, CourtDecision.document_id == LegalDocument.id)
            .where(LegalDocument.source == SOURCE)
            .order_by(CourtDecision.adopted_on)
        )
        return [
            Decision(
                eqanun_id=doc.source_id,
                title=doc.title,
                adopted_on=decision.adopted_on,
                outcome=decision.outcome,
                operative=decision.operative,
                # The title names the provisions ruled on; the operative part can rule on more
                # (decision 48189 decides which ground of Art. 70 applies to a branch closure).
                interprets=tuple(
                    dict.fromkeys(
                        interpreted_articles(doc.title, act_names)
                        + interpreted_articles(decision.operative, act_names)
                    )
                ),
            )
            for doc, decision in rows
        ]


def _az_date(day: date) -> str:
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}-ci il"


def decision_hit(decision: Decision) -> Hit:
    """A decision shaped like a retrieved source, so answerers cite and verify it the same way.

    The first line is the breadcrumb: ``source_title`` shows its last element.
    """
    heading = (
        f"Konstitusiya Məhkəməsi Plenumunun {_az_date(decision.adopted_on)} tarixli qərarı: "
        f"{decision.title}"
    )
    text = f"Konstitusiya Məhkəməsinin qərarları > {heading}\nQƏRARA ALDI:\n{decision.operative}"
    return Hit(
        key=decision.key,
        article_key=decision.key,
        parent_key=None,
        text=text,
        score=0.0,
        valid_from=decision.adopted_on,
        cites=decision.interprets,
    )


def with_decisions(hits: Sequence[Hit], decisions: Sequence[Decision]) -> list[Hit]:
    """Each decision right after the first source it interprets; each decision once."""
    by_article: dict[str, list[Decision]] = {}
    for d in decisions:
        for key in d.interprets:
            by_article.setdefault(key, []).append(d)
    out: list[Hit] = []
    placed: set[int] = set()
    for hit in hits:
        out.append(hit)
        for d in by_article.get(hit.article_key, ()):
            if d.eqanun_id not in placed:
                placed.add(d.eqanun_id)
                out.append(decision_hit(d))
    return out


async def attach_decisions(
    hits: Sequence[Hit], store: DecisionStore, *, source_id: int, as_of: date
) -> list[Hit]:
    """Add the decisions in force on ``as_of`` that interpret the retrieved articles.

    Like graph-aware search, this improves answers but must not break them: if the graph is
    unreachable the answer is built from the articles alone, and the outage is logged.
    """
    keys = list(dict.fromkeys(h.article_key for h in hits))
    try:
        decisions = await store.decisions_for(source_id, keys, as_of=as_of)
    except Exception:
        log.warning("decisions_unavailable", exc_info=True)
        return list(hits)
    return with_decisions(hits, decisions)
