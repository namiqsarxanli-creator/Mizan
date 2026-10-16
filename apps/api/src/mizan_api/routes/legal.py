"""Legal search and question answering."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from mizan_retrieval.answer import AnswerBackend, AnswerUnavailableError, source_title
from mizan_retrieval.search import Hit, SearchBackend, SearchConfig

router = APIRouter(prefix="/v1", tags=["legal"])


def today_in_baku() -> date:
    return datetime.now(ZoneInfo("Asia/Baku")).date()


class Source(BaseModel):
    key: str = Field(examples=["art:69/part:1"])
    title: str = Field(examples=["Maddə 69. Əmək müqaviləsinə işçinin təşəbbüsü ilə xitam"])
    text: str
    score: float
    in_force_since: date


class SearchResponse(BaseModel):
    query: str
    as_of: date
    retrieval: str
    results: list[Source]


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    as_of: date | None = None
    k: int = Field(default=6, ge=1, le=12)


class CitationOut(BaseModel):
    key: str
    title: str
    quote: str


class AskResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    sources: list[Source]
    as_of: date
    model: str
    refused: bool
    # Citations the model produced that did not match its sources and were discarded.
    rejected_citations: int = 0
    disclaimer: str = "Bu cavab hüquqi məsləhət deyil."


def _source(hit: Hit) -> Source:
    return Source(
        key=hit.key,
        title=source_title(hit),
        text=hit.text,
        score=hit.score,
        in_force_since=hit.valid_from,
    )


def _searcher(request: Request) -> tuple[SearchBackend, SearchConfig, int]:
    state = request.app.state
    return state.searcher, state.search_config, state.settings.retrieval.source_id


@router.get("/search")
async def search(
    request: Request,
    q: Annotated[str, Query(min_length=2, max_length=500, description="Sual və ya açar sözlər")],
    as_of: Annotated[date | None, Query(description="Bu tarixdə qüvvədə olan mətn")] = None,
    k: Annotated[int, Query(ge=1, le=20)] = 10,
) -> SearchResponse:
    searcher, config, source_id = _searcher(request)
    on = as_of or today_in_baku()
    hits = await searcher.search(q, source_id=source_id, as_of=on, config=config, k=k)
    return SearchResponse(
        query=q, as_of=on, retrieval=config.label, results=[_source(h) for h in hits]
    )


@router.post("/ask", responses={503: {"description": "LLM is not configured"}})
async def ask(request: Request, body: AskRequest) -> AskResponse:
    answerer: AnswerBackend | None = request.app.state.answerer
    if answerer is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Answering is disabled: set MIZAN_LLM__GROQ_API_KEY (or switch MIZAN_LLM__PROVIDER). "
            "/v1/search still works.",
        )
    searcher, config, source_id = _searcher(request)
    state = request.app.state
    on = body.as_of or today_in_baku()
    query = await state.rewriter.expand(body.question) if state.rewriter else body.question
    hits = await searcher.search(query, source_id=source_id, as_of=on, config=config, k=body.k)
    if state.expand is not None:
        hits = await state.expand(hits, source_id, on)
    try:
        answer = await answerer.answer(body.question, hits, as_of=on)
    except AnswerUnavailableError as exc:
        headers = {"Retry-After": str(exc.retry_after_s)} if exc.retry_after_s else None
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Answering is temporarily unavailable: {exc}. /v1/search still works.",
            headers=headers,
        ) from exc
    return AskResponse(
        answer=answer.text,
        citations=[CitationOut(key=c.key, title=c.title, quote=c.quote) for c in answer.citations],
        sources=[_source(h) for h in hits],
        as_of=on,
        model=answer.model,
        refused=answer.refused,
        rejected_citations=answer.rejected_citations,
    )
