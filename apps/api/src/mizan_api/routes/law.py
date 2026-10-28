"""Direct access to provisions and their collisions: the tools behind the agent, without an LLM."""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field

from mizan_agents.tools import LegalTools
from mizan_api.routes.legal import today_in_baku
from mizan_graph.decisions import Decision

router = APIRouter(prefix="/v1", tags=["law"])

_ARTICLE_KEY = r"^(art|annex):[0-9]+(-[0-9]+)?$"


class DecisionOut(BaseModel):
    key: str = Field(examples=["kmq:61412"])
    title: str
    adopted_on: date
    outcome: str = Field(examples=["unconstitutional"])
    interprets: list[str]


class ArticleResponse(BaseModel):
    key: str = Field(examples=["art:144"])
    title: str
    text: str
    in_force_since: date
    as_of: date
    # Constitutional Court decisions interpreting this article, adopted on or before ``as_of``.
    decisions: list[DecisionOut]


class ResolutionOut(BaseModel):
    applies: str | None = Field(description="The provision that prevails; null if both apply")
    rule: str = Field(examples=["lex specialis"])
    why: str
    basis: str = Field(examples=["NHA Konstitusiya Qanunu 10.4"])


class ConflictsResponse(BaseModel):
    keys: list[str]
    as_of: date
    resolutions: list[ResolutionOut]


def _tools(request: Request) -> LegalTools:
    tools: LegalTools | None = request.app.state.tools
    if tools is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "the citation graph is not configured"
        )
    return tools


def _decision(d: Decision) -> DecisionOut:
    return DecisionOut(
        key=d.key,
        title=d.title,
        adopted_on=d.adopted_on,
        outcome=d.outcome,
        interprets=list(d.interprets),
    )


@router.get(
    "/articles/{key}",
    responses={
        404: {"description": "No such article in force on the date"},
        503: {"description": "Graph not configured"},
    },
)
async def get_article(
    request: Request,
    key: Annotated[str, Path(pattern=_ARTICLE_KEY, examples=["art:144"])],
    as_of: Annotated[date | None, Query(description="Bu tarixdə qüvvədə olan mətn")] = None,
) -> ArticleResponse:
    """The whole article as in force on ``as_of``, with the Court decisions that interpret it."""
    tools = _tools(request)
    on = as_of or today_in_baku()
    found = await tools.get_article(key, as_of=on)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{key} is not in force on {on}")
    decisions = await tools.get_court_decisions([key], as_of=on)
    return ArticleResponse(
        key=found.key,
        title=found.title,
        text=found.text,
        in_force_since=found.in_force_since,
        as_of=on,
        decisions=[_decision(d) for d in decisions],
    )


@router.get("/conflicts", responses={503: {"description": "Graph not configured"}})
async def check_conflicts(
    request: Request,
    keys: Annotated[
        list[str],
        Query(min_length=1, max_length=20, description="Article keys, e.g. art:51 art:52"),
    ],
    as_of: Annotated[date | None, Query()] = None,
) -> ConflictsResponse:
    """Collisions among the given articles: Code exceptions (lex specialis), voided provisions."""
    for key in keys:
        if re.fullmatch(_ARTICLE_KEY, key) is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"not an article key: {key}")
    tools = _tools(request)
    on = as_of or today_in_baku()
    decisions = await tools.get_court_decisions(keys, as_of=on)
    results = await tools.check_conflicts(keys, decisions, as_of=on)
    return ConflictsResponse(
        keys=keys,
        as_of=on,
        resolutions=[
            ResolutionOut(
                applies=r.applies.provision if r.applies else None,
                rule=r.rule.value,
                why=r.why,
                basis=r.basis,
            )
            for r in results
        ],
    )
